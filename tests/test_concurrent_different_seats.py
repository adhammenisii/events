"""Required test: concurrent bookings for different seats all succeed.

The counterpart to the same-seat test. Correctness is easy to get by
serialising everything; this checks that unrelated bookings are not blocked
by each other, which is what makes the guarantee useful rather than merely
safe.
"""

import threading
import time
from collections import Counter

from support import TemporaryDatabase, available_seats, run_tests, user_ids

from boxoffice.errors import BoxOfficeError
from boxoffice.repositories import SeatRepository
from boxoffice.services import BookingService

EVENT_ID = "EVT00007"
CONCURRENT_BOOKINGS = 25


def test_all_distinct_seat_bookings_succeed():
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        seats = available_seats(db, EVENT_ID, CONCURRENT_BOOKINGS)
        users = user_ids(db, CONCURRENT_BOOKINGS)

        outcomes, elapsed = _book_together(booking, list(zip(users, seats)))
        tally = Counter(outcomes)

        print(f"\n  {CONCURRENT_BOOKINGS} threads, {CONCURRENT_BOOKINGS} distinct seats: "
              f"{dict(tally)} in {elapsed * 1000:.0f} ms")
        assert tally["booking_successful"] == CONCURRENT_BOOKINGS, (
            f"every booking should have succeeded, got {dict(tally)}"
        )


def test_each_seat_is_owned_by_its_own_booker():
    """No crossed wires: seat i must belong to user i, not to whoever raced past."""
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        seats = available_seats(db, EVENT_ID, CONCURRENT_BOOKINGS)
        users = user_ids(db, CONCURRENT_BOOKINGS)
        pairs = list(zip(users, seats))

        _book_together(booking, pairs)

        with db.read() as connection:
            repository = SeatRepository(connection)
            for user_id, seat in pairs:
                stored = repository.get(seat.seat_id)
                assert stored.status == "booked", f"{seat.seat_id} was not booked"
                assert stored.booked_by_user_id == user_id, (
                    f"{seat.seat_id} belongs to {stored.booked_by_user_id}, expected {user_id}"
                )


def test_event_statistics_account_for_every_booking():
    """The counters the UI shows must agree with what actually happened."""
    with TemporaryDatabase() as db:
        from boxoffice.repositories import EventRepository

        booking = BookingService(db)
        seats = available_seats(db, EVENT_ID, CONCURRENT_BOOKINGS)
        users = user_ids(db, CONCURRENT_BOOKINGS)

        with db.read() as connection:
            before = EventRepository(connection).stats(EVENT_ID)

        _book_together(booking, list(zip(users, seats)))

        with db.read() as connection:
            after = EventRepository(connection).stats(EVENT_ID)

        assert after.booked_seats == before.booked_seats + CONCURRENT_BOOKINGS
        assert after.available_seats == before.available_seats - CONCURRENT_BOOKINGS
        expected_revenue = before.revenue + sum(seat.price for seat in seats)
        assert abs(after.revenue - expected_revenue) < 0.01, (
            f"revenue {after.revenue} does not match expected {expected_revenue}"
        )


def _book_together(booking: BookingService, pairs) -> tuple[list[str], float]:
    outcomes: list[str | None] = [None] * len(pairs)
    barrier = threading.Barrier(len(pairs))

    def attempt(index: int, user_id: str, seat) -> None:
        barrier.wait()
        try:
            outcomes[index] = booking.book_seat(
                user_id=user_id, event_id=seat.event_id, seat_id=seat.seat_id
            ).status
        except BoxOfficeError as error:
            outcomes[index] = error.details.get("result", error.code)

    threads = [
        threading.Thread(target=attempt, args=(index, user_id, seat))
        for index, (user_id, seat) in enumerate(pairs)
    ]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    elapsed = time.perf_counter() - started

    assert all(outcome is not None for outcome in outcomes), "a booking thread hung"
    return outcomes, elapsed


if __name__ == "__main__":
    raise SystemExit(run_tests(dict(globals())))
