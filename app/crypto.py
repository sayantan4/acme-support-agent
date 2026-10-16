"""Encrypt OAuth tokens at rest. Without TOKEN_ENCRYPTION_KEY, tokens are stored in plaintext (dev only)."""

import logging

from cryptography.fernet import Fernet

from app.config import get_settings

log = logging.getLogger(__name__)
_PLAIN_PREFIX = "plain:"  # marks a value stored without encryption


def _fernet() -> Fernet | None:
    key = get_settings().token_encryption_key
    return Fernet(key.encode()) if key else None


def encrypt(value: str) -> str:
    fernet = _fernet()
    if fernet is None:
        log.warning("TOKEN_ENCRYPTION_KEY not set; storing OAuth tokens unencrypted")
        return _PLAIN_PREFIX + value
    return fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if value.startswith(_PLAIN_PREFIX):
        return value[len(_PLAIN_PREFIX):]
    fernet = _fernet()
    if fernet is None:
        raise RuntimeError("Stored token is encrypted but TOKEN_ENCRYPTION_KEY is not set")
    return fernet.decrypt(value.encode()).decode()
