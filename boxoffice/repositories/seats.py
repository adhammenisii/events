"""Queries against the ``seats`` table, including the booking write itself."""

import sqlite3

from ..models import Seat

_SEAT_COLUMNS = """
    seat_id, event_id, section, row_label, seat_number,
    price, status, booked_by_user_id, booked_at
"""

# Sections are lettered (A, B, C, VIP) or named (Floor); ordering them
# alphabetically would put "Floor" between C and VIP for no reason. This puts
# the premium sections first and leaves the rest alphabetical, matching how a
# printed seating chart is laid out.
_SECTION_ORDER = """
    CASE section
        WHEN 'VIP'   THEN 0
        WHEN 'Floor' THEN 1
        ELSE 2
    END, section
"""


class SeatRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def list_for_event(self, event_id: str) -> list[Seat]:
        rows = self._connection.execute(
            f"""
            SELECT {_SEAT_COLUMNS}
              FROM seats
             WHERE event_id = ?
             ORDER BY {_SECTION_ORDER}, CAST(row_label AS INTEGER), row_label, seat_number
            """,
            (event_id,),
        ).fetchall()
        return [Seat.from_row(row) for row in rows]

    def list_all(self) -> list[Seat]:
        """Every seat, in a stable order -- used by the storage export."""
        rows = self._connection.execute(
            f"SELECT {_SEAT_COLUMNS} FROM seats ORDER BY event_id, seat_id"
        ).fetchall()
        return [Seat.from_row(row) for row in rows]

    def list_for_user(self, user_id: str) -> list[Seat]:
        rows = self._connection.execute(
            f"""
            SELECT {_SEAT_COLUMNS}
              FROM seats
             WHERE booked_by_user_id = ?
             ORDER BY event_id, {_SECTION_ORDER}, seat_number
            """,
            (user_id,),
        ).fetchall()
        return [Seat.from_row(row) for row in rows]

    def get(self, seat_id: str) -> Seat | None:
        row = self._connection.execute(
            f"SELECT {_SEAT_COLUMNS} FROM seats WHERE seat_id = ?", (seat_id,)
        ).fetchone()
        return Seat.from_row(row) if row else None

    def claim(self, seat_id: str, user_id: str, booked_at: str) -> bool:
        """Book a seat if and only if it is still free. Returns whether we won.

        The ``status = 'available'`` predicate is the whole concurrency
        guarantee. Two transactions running this statement for the same seat
        are serialised by SQLite; the first flips the row and reports one
        affected row, the second matches nothing and reports zero. There is no
        window between the check and the write for a second booker to slip
        into, because there is no separate check.
        """
        cursor = self._connection.execute(
            """
            UPDATE seats
               SET status = 'booked', booked_by_user_id = ?, booked_at = ?
             WHERE seat_id = ? AND status = 'available'
            """,
            (user_id, booked_at, seat_id),
        )
        return cursor.rowcount == 1

    def release(self, seat_id: str, user_id: str) -> bool:
        """Cancel a booking, but only the caller's own. Returns whether we did.

        Ownership is part of the WHERE clause for the same reason: checking it
        in Python first would leave a gap in which the booking could change
        hands underneath us.
        """
        cursor = self._connection.execute(
            """
            UPDATE seats
               SET status = 'available', booked_by_user_id = NULL, booked_at = NULL
             WHERE seat_id = ? AND status = 'booked' AND booked_by_user_id = ?
            """,
            (seat_id, user_id),
        )
        return cursor.rowcount == 1

    def upsert_many(self, seats: list[dict]) -> int:
        """Bulk-load seats during seeding.

        Existing seats keep their live booking state -- only the static layout
        columns are refreshed, so re-running the seed never wipes real
        bookings made through the application.
        """
        self._connection.executemany(
            """
            INSERT INTO seats (seat_id, event_id, section, row_label, seat_number,
                               price, status, booked_by_user_id, booked_at)
            VALUES (:seat_id, :event_id, :section, :row_label, :seat_number,
                    :price, :status, :booked_by_user_id, :booked_at)
            ON CONFLICT (seat_id) DO UPDATE SET
                section     = excluded.section,
                row_label   = excluded.row_label,
                seat_number = excluded.seat_number,
                price       = excluded.price
            """,
            seats,
        )
        return len(seats)
