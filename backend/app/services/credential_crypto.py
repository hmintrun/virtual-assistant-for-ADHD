"""Module mã hóa bảo mật credential Google Calendar bằng Fernet Envelope.

Tuân thủ Mục 4.5 trong Hướng dẫn tích hợp Google Calendar hai chiều:
- Định dạng lưu trữ: `fernet:v1:<ciphertext>`
- Ngăn chặn lưu token dạng plaintext
- Xác thực liên kết (binding) giữa user_id và connection_id sau khi giải mã để chống tấn công tráo ciphertext.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)

ENVELOPE_PREFIX = "fernet:v1:"


def _get_fernet_key() -> bytes:
    """Dùng khóa Fernet tường minh khi triển khai. Dev/test có thể dẫn xuất từ SECRET_KEY."""
    configured_key = settings.calendar_credential_key()
    if configured_key is not None:
        return configured_key
    if settings.CALENDAR_CREDENTIAL_SECRET_KEY.strip():
        logger.warning("[Crypto] CALENDAR_CREDENTIAL_SECRET_KEY không hợp lệ, chuyển sang fallback.")

    # Fallback cho môi trường dev/fake: băm SHA-256 từ SECRET_KEY để sinh 32 bytes base64
    derived = hashlib.sha256(f"calendar-fernet:{settings.SECRET_KEY}".encode()).digest()
    return base64.urlsafe_b64encode(derived)


def encrypt_credentials(payload: dict[str, Any]) -> str:
    """Mã hóa payload JSON của token thành chuỗi envelope fernet:v1:<ciphertext>."""
    key = _get_fernet_key()
    f = Fernet(key)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ciphertext = f.encrypt(serialized).decode("ascii")
    return f"{ENVELOPE_PREFIX}{ciphertext}"


def decrypt_credentials(
    envelope: str,
    *,
    expected_user_id: str | None = None,
    expected_connection_id: str | None = None,
) -> dict[str, Any]:
    """Giải mã chuỗi envelope fernet:v1:<ciphertext> và kiểm tra tính toàn vẹn binding.

    Raises:
        ValueError: Nếu định dạng envelope không đúng hoặc binding tenant bị sai lệch.
        InvalidToken: Nếu khóa sai hoặc dữ liệu bị sửa đổi / giả mạo.
    """
    if not envelope.startswith(ENVELOPE_PREFIX):
        raise ValueError(f"Định dạng envelope credential không hợp lệ (thiếu tiền tố {ENVELOPE_PREFIX})")

    ciphertext = envelope[len(ENVELOPE_PREFIX) :].encode("ascii")
    key = _get_fernet_key()
    f = Fernet(key)

    try:
        decrypted_bytes = f.decrypt(ciphertext)
    except InvalidToken as err:
        logger.error("[Crypto] Giải mã credential thất bại (InvalidToken hoặc sai khóa bí mật).")
        raise ValueError("Không thể giải mã credential (khóa không đúng hoặc token bị hỏng)") from err

    payload = json.loads(decrypted_bytes.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Dữ liệu sau giải mã không phải là JSON object")

    # Kiểm tra Tenant Binding chống tráo ciphertext giữa các tài khoản khác nhau
    if expected_user_id and str(payload.get("user_id", "")) != str(expected_user_id):
        raise ValueError(
            f"Vi phạm bảo mật tenant binding: user_id trong token ({payload.get('user_id')}) "
            f"không khớp với user_id yêu cầu ({expected_user_id})"
        )

    if expected_connection_id and str(payload.get("connection_id", "")) != str(expected_connection_id):
        raise ValueError(
            f"Vi phạm bảo mật connection binding: connection_id trong token ({payload.get('connection_id')}) "
            f"không khớp với connection_id yêu cầu ({expected_connection_id})"
        )

    return payload
