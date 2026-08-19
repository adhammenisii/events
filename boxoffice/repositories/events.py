"""Queries against the ``events`` table."""

import sqlite3

from ..models import Event, EventStats

_EVENT_COLUMNS = """
    event_id, name, category, venue, city,
    event_date, event_time, total_seats, base_price
"""


class EventRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def list_all(self) -> list[Event]:
        rows = self._connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM events ORDER BY event_date, event_time, event_id"
        ).fetchall()
        return [Event.from_row(row) for row in rows]

    def get(self, event_id: str) -> Event | None:
        row = self._connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return Event.from_row(row) if row else None

    def exists(self, event_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return row is not None

    def stats(self, event_id: str) -> EventStats:
        """Occupancy and booked revenue for one event, straight from the rows.

        Deriving this in SQL rather than caching a counter is what keeps the
        figures honest: they cannot drift away from the seats they describe,
        whichever process did the booking.
        """
        row = self._connection.execute(
            """
            SELECT COUNT(*)                                                  AS total,
                   COALESCE(SUM(status = 'booked'), 0)                       AS booked,
                   COALESCE(SUM(CASE WHEN status = 'booked' THEN price END), 0) AS revenue
              FROM seats
             WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
        return EventStats(
            total_seats=row["total"], booked_seats=row["booked"], revenue=row["revenue"]
        )

    def stats_by_event(self) -> dict[str, EventStats]:
        """One pass for every event, for the event picker's availability hints."""
        rows = self._connection.execute(
            """
            SELECT event_id,
                   COUNT(*)                                                  AS total,
                   COALESCE(SUM(status = 'booked'), 0)                       AS booked,
                   COALESCE(SUM(CASE WHEN status = 'booked' THEN price END), 0) AS revenue
              FROM seats
             GROUP BY event_id
            """
        ).fetchall()
        return {
            row["event_id"]: EventStats(
                total_seats=row["total"], booked_seats=row["booked"], revenue=row["revenue"]
            )
            for row in rows
        }

    def upsert_many(self, events: list[dict]) -> int:
        """Bulk-load events during seeding; existing rows are refreshed."""
        self._connection.executemany(
            """
            INSERT INTO events (event_id, name, category, venue, city,
                                event_date, event_time, total_seats, base_price)
            VALUES (:event_id, :name, :category, :venue, :city,
                    :event_date, :event_time, :total_seats, :base_price)
            ON CONFLICT (event_id) DO UPDATE SET
                name        = excluded.name,
                category    = excluded.category,
                venue       = excluded.venue,
                city        = excluded.city,
                event_date  = excluded.event_date,
                event_time  = excluded.event_time,
                total_seats = excluded.total_seats,
                base_price  = excluded.base_price
            """,
            events,
        )
        return len(events)
