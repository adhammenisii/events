"""Queries against the ``booking_log`` table.

Every attempt is recorded, not just the ones that worked. A rejected booking
is the only evidence that two requests raced, so it is the row you want when
someone asks why their click appeared to do nothing.
"""

import sqlite3


class BookingLogRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def record(
        self,
        *,
        created_at: str,
        user_id: str,
        event_id: str,
        seat_id: str,
        action: str,
        result: str,
        message: str,
    ) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO booking_log (created_at, user_id, event_id, seat_id, action, result, message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (created_at, user_id, event_id, seat_id, action, result, message),
        )
        return cursor.lastrowid

    def entries_after(self, entry_id: int, limit: int = 5_000) -> list[dict]:
        """Rows newer than ``entry_id`` -- how the storage export tails the log."""
        rows = self._connection.execute(
            """
            SELECT entry_id, created_at, user_id, event_id, seat_id, action, result, message
              FROM booking_log
             WHERE entry_id > ?
             ORDER BY entry_id
             LIMIT ?
            """,
            (entry_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_entry_id(self) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(entry_id), 0) AS latest FROM booking_log"
        ).fetchone()
        return row["latest"]

    def recent_for_user(self, user_id: str, limit: int = 20) -> list[dict]:
        rows = self._connection.execute(
            """
            SELECT created_at, event_id, seat_id, action, result, message
              FROM booking_log
             WHERE user_id = ?
             ORDER BY entry_id DESC
             LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
