"""Accounts and sessions.

"Booking as" used to be a dropdown the browser filled in, which meant the
client chose whose name a booking was made under. It is now derived from a
server-side session: the browser holds an opaque token, and the identity
attached to a booking is looked up here, never sent by the client.
"""

import logging
import re
import threading
import time
from dataclasses import dataclass

from ..clock import utc_iso_after, utc_now_iso
from ..config import SESSION_TTL_SECONDS
from ..db import Database
from ..errors import AuthenticationError, DuplicateAccountError, ValidationError
from ..models import User
from ..passwords import (
    hash_password,
    new_session_token,
    validate_password_strength,
    verify_password,
)
from ..repositories import SessionRepository, UserRepository

logger = logging.getLogger(__name__)

# Deliberately permissive: enough to catch a typo like a missing "@", without
# rejecting the many addresses that are valid but unusual.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")
MAX_NAME_LENGTH = 80

# Failed logins allowed per email within the window before further attempts
# are refused. Guessing a password at five tries per five minutes is not a
# viable attack, and a legitimate user rarely needs a sixth.
MAX_FAILED_LOGINS = 5
LOGIN_WINDOW_SECONDS = 300

# Compared against when an email has no account, so that a wrong address and
# a wrong password take the same amount of time to reject and cannot be told
# apart by an attacker enumerating addresses.
_TIMING_DECOY_HASH, _TIMING_DECOY_SALT = hash_password(new_session_token())


@dataclass(frozen=True, slots=True)
class Session:
    user: User
    token: str
    expires_at: str


class LoginThrottle:
    """In-memory sliding window of recent failures, keyed by email.

    Process-local by design: this instance guards this process, and the
    bounded work it prevents is a password hash. A deployment behind several
    workers would move the counter to whatever they already share.
    """

    def __init__(self, limit: int = MAX_FAILED_LOGINS, window: int = LOGIN_WINDOW_SECONDS):
        self._limit = limit
        self._window = window
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key)) >= self._limit

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._recent(key).append(time.monotonic())

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def _recent(self, key: str) -> list[float]:
        """Drop timestamps that have aged out, and return what is left.

        Pruning on access is what keeps the dictionary from growing without
        bound; keys stop being touched once they expire and are removed on the
        next attempt for that email.
        """
        cutoff = time.monotonic() - self._window
        recent = [at for at in self._failures.get(key, []) if at > cutoff]
        self._failures[key] = recent
        return recent


class AuthService:
    def __init__(self, db: Database, throttle: LoginThrottle | None = None):
        self._db = db
        self._throttle = throttle or LoginThrottle()

    def log_in(self, email: str, password: str) -> Session:
        email = _clean_email(email)
        if self._throttle.is_blocked(email):
            raise AuthenticationError(
                "Too many failed attempts for this account. Try again in a few minutes."
            )

        with self._db.read() as connection:
            credentials = UserRepository(connection).credentials_for_email(email)

        if credentials is None:
            # Spend the same time as a real verification would, then fail.
            verify_password(password, _TIMING_DECOY_HASH, _TIMING_DECOY_SALT)
            self._throttle.record_failure(email)
            raise AuthenticationError("That email and password do not match an account.")

        user_id, stored_hash, salt = credentials
        if not verify_password(password, stored_hash, salt):
            self._throttle.record_failure(email)
            logger.info("Failed login for %s", email)
            raise AuthenticationError("That email and password do not match an account.")

        self._throttle.clear(email)
        return self._start_session(user_id)

    def register(self, full_name: str, email: str, password: str) -> Session:
        full_name = _clean_name(full_name)
        email = _clean_email(email)
        validate_password_strength(password)
        password_hash, salt = hash_password(password)

        with self._db.write() as connection:
            users = UserRepository(connection)
            if users.email_taken(email):
                raise DuplicateAccountError("An account already uses that email address.")
            user = users.create(
                user_id=users.next_user_id(),
                full_name=full_name,
                email=email,
                password_hash=password_hash,
                password_salt=salt,
                signup_date=utc_now_iso(),
            )
        logger.info("Registered account %s", user.user_id)
        return self._start_session(user.user_id)

    def resolve_session(self, token: str | None) -> User | None:
        """Return the signed-in user for a cookie value, or ``None``."""
        if not token:
            return None
        with self._db.read() as connection:
            return SessionRepository(connection).user_for_token(token, utc_now_iso())

    def log_out(self, token: str | None) -> bool:
        if not token:
            return False
        with self._db.write() as connection:
            return SessionRepository(connection).delete(token)

    def demo_accounts(self, limit: int = 6) -> list[User]:
        """Seeded accounts that can be signed into, listed on the login page."""
        with self._db.read() as connection:
            return UserRepository(connection).list_with_login(limit)

    def purge_expired_sessions(self) -> int:
        with self._db.write() as connection:
            removed = SessionRepository(connection).delete_expired(utc_now_iso())
        if removed:
            logger.info("Purged %d expired session(s).", removed)
        return removed

    def _start_session(self, user_id: str) -> Session:
        token = new_session_token()
        expires_at = utc_iso_after(SESSION_TTL_SECONDS)
        with self._db.write() as connection:
            SessionRepository(connection).create(
                token=token, user_id=user_id,
                created_at=utc_now_iso(), expires_at=expires_at,
            )
            user = UserRepository(connection).get(user_id)
        return Session(user=user, token=token, expires_at=expires_at)


def _clean_email(email: str) -> str:
    email = (email or "").strip()
    if not EMAIL_PATTERN.match(email):
        raise ValidationError("Enter a valid email address.", details={"field": "email"})
    return email


def _clean_name(full_name: str) -> str:
    full_name = " ".join((full_name or "").split())
    if len(full_name) < 2:
        raise ValidationError("Enter your full name.", details={"field": "full_name"})
    if len(full_name) > MAX_NAME_LENGTH:
        raise ValidationError(
            f"Name must be under {MAX_NAME_LENGTH} characters.", details={"field": "full_name"}
        )
    return full_name
