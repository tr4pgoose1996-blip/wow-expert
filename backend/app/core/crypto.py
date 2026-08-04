"""Symmetric encryption for OAuth refresh tokens stored at rest."""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class TokenCipherError(RuntimeError):
    """Raised when a stored token cannot be decrypted."""


@lru_cache
def _cipher() -> Fernet:
    """Build the Fernet cipher from configuration.

    A dedicated ``BLIZZARD_TOKEN_ENCRYPTION_KEY`` is strongly preferred. When
    absent outside production we derive a stable key from ``SECRET_KEY`` so
    local development works without extra setup; production refuses to start
    without an explicit key, since rotating ``SECRET_KEY`` would otherwise
    silently invalidate every stored Battle.net grant.
    """
    configured = settings.BLIZZARD_TOKEN_ENCRYPTION_KEY
    if configured:
        try:
            return Fernet(configured.encode())
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                "BLIZZARD_TOKEN_ENCRYPTION_KEY is not a valid Fernet key. "
                "Generate one with: python -c \"from cryptography.fernet "
                "import Fernet; print(Fernet.generate_key().decode())\""
            ) from exc

    if settings.is_production:
        raise RuntimeError(
            "BLIZZARD_TOKEN_ENCRYPTION_KEY must be set in production."
        )

    logger.warning(
        "BLIZZARD_TOKEN_ENCRYPTION_KEY is unset; deriving a development key "
        "from SECRET_KEY. Do not use this configuration in production."
    )
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token for database storage."""
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a stored token.

    Raises:
        TokenCipherError: if the value was encrypted under a different key or
            has been tampered with. Callers should treat this as a revoked
            grant and prompt the user to reconnect.
    """
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise TokenCipherError(
            "Stored token could not be decrypted; the encryption key may "
            "have changed."
        ) from exc
