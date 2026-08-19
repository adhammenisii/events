"""Queries against the ``sessions`` table.

Sessions are server-side rows rather than signed cookies: logging out, or
revoking every session for an account, is then a DELETE rather than a token
blacklist. The cookie carries nothing but an opaque random token.
"""

import sqlite3

from ..models import User


class SessionRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def create(self, *, token: str, user_id: str, created_at: str, expires_at: str) -> None:
        self._connection.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, created_at, expires_at),
        )

    def user_for_token(self, token: str, now: str) -> User | None:
        """Resolve a session cookie to its owner, ignoring expired rows."""
        row = self._connection.execute(
            """
            SELECT u.user_id, u.full_name, u.email
              FROM sessions s
              JOIN users u ON u.user_id = s.user_id
             WHERE s.token = ? AND s.expires_at > ?
            """,
            (token, now),
        ).fetchone()
        return User.from_row(row) if row else None

    def delete(self, token: str) -> bool:
        cursor = self._connection.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return cursor.rowcount > 0

    def delete_expired(self, now: str) -> int:
        cursor = self._connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        return cursor.rowcount
