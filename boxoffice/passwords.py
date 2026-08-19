"""Password hashing and opaque token generation.

PBKDF2-HMAC-SHA256 from the standard library, so the project keeps its single
third-party dependency (Flask). Each password gets its own random salt, and
verification is a constant-time comparison so a wrong password cannot be
narrowed down by timing.
"""

import hashlib
import hmac
import secrets

from .config import PASSWORD_HASH_ITERATIONS
from .errors import ValidationError

SALT_BYTES = 16
TOKEN_BYTES = 32
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256


def hash_password(password: str, *, salt: str | None = None) -> tuple[str, str]:
    """Return ``(hash_hex, salt_hex)`` for a plaintext password."""
    salt = salt or secrets.token_hex(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PASSWORD_HASH_ITERATIONS,
    )
    return digest.hex(), salt


def verify_password(password: str, expected_hash: str, salt: str) -> bool:
    """Check a plaintext password against a stored hash and salt.

    A malformed salt means a corrupt row rather than a wrong password, but the
    caller only ever needs to know that the attempt failed.
    """
    try:
        candidate, _ = hash_password(password, salt=salt)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, expected_hash)


def new_session_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def validate_password_strength(password: str) -> None:
    """Reject passwords that are too short or absurdly long.

    The upper bound matters as much as the lower one: PBKDF2 hashes whatever
    it is given, so an unbounded password is an unbounded amount of work per
    login attempt.
    """
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationError(f"Password must be under {MAX_PASSWORD_LENGTH} characters.")
