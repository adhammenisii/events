"""Read-side business logic: what is on sale and how full it is.

Nothing here mutates state, so every method runs on a read connection and can
be served from a replica if this ever outgrows a single file.
"""

from ..db import Database
from ..errors import NotFoundError
from ..models import Event, EventStats, Seat
from ..repositories import EventRepository, SeatRepository


class CatalogService:
    def __init__(self, db: Database):
        self._db = db

    def list_events(self) -> list[dict]:
        """Every event, each carrying its own live availability counters.

        The counters come from that event's seat rows rather than from the
        ``total_seats`` column, so an event whose layout has changed reports
        what is really in the venue.
        """
        with self._db.read() as connection:
            events = EventRepository(connection).list_all()
            stats = EventRepository(connection).stats_by_event()

        return [
            {
                **event.to_dict(),
                "stats": stats.get(
                    event.event_id, EventStats(0, 0, 0.0)
                ).to_dict(),
            }
            for event in events
        ]

    def get_event(self, event_id: str) -> Event:
        with self._db.read() as connection:
            event = EventRepository(connection).get(event_id)
        if event is None:
            raise NotFoundError(f"No event with id {event_id}.")
        return event

    def seat_map(self, event_id: str) -> tuple[Event, list[Seat], EventStats]:
        """Everything one render of the seating chart needs, in a single read.

        Event, seats and statistics are read on the same connection so the
        header cannot disagree with the seats drawn underneath it.
        """
        with self._db.read() as connection:
            events = EventRepository(connection)
            event = events.get(event_id)
            if event is None:
                raise NotFoundError(f"No event with id {event_id}.")
            seats = SeatRepository(connection).list_for_event(event_id)
            stats = events.stats(event_id)
        return event, seats, stats

    def event_stats(self, event_id: str) -> EventStats:
        with self._db.read() as connection:
            events = EventRepository(connection)
            if not events.exists(event_id):
                raise NotFoundError(f"No event with id {event_id}.")
            return events.stats(event_id)

    def seats_held_by(self, user_id: str) -> list[Seat]:
        with self._db.read() as connection:
            return SeatRepository(connection).list_for_user(user_id)
