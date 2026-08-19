"""Required test: many concurrent bookings for one seat, exactly one wins.

The threads are released together by a Barrier so they genuinely overlap
rather than running one after another very quickly -- without it the test
would pass even with no concurrency control at all.
"""

import threading
from collections import Counter

from support import TemporaryDatabase, available_seats, run_tests, user_ids

from boxoffice.errors import BoxOfficeError
from boxoffice.repositories import BookingLogRepository
from boxoffice.services import BookingService

EVENT_ID = "EVT00003"
CONCURRENT_USERS = 25


def test_only_one_of_many_concurrent_bookings_succeeds():
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        seat = available_seats(db, EVENT_ID)[0]
        users = user_ids(db, CONCURRENT_USERS)

        outcomes = _book_together(
            booking, [(user, EVENT_ID, seat.seat_id) for user in users]
        )
        tally = Counter(outcomes)

        print(f"\n  {CONCURRENT_USERS} threads, one seat ({seat.seat_id}): {dict(tally)}")
        assert tally["booking_successful"] == 1, (
            f"expected exactly one winner, got {tally['booking_successful']}"
        )
        assert tally["seat_already_booked"] == CONCURRENT_USERS - 1, (
            f"expected {CONCURRENT_USERS - 1} clean rejections, got {dict(tally)}"
        )


def test_seat_ends_in_a_consistent_state():
    """Whoever won, the row must name them -- and only them."""
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        seat = available_seats(db, EVENT_ID)[0]
        users = user_ids(db, CONCURRENT_USERS)

        _book_together(booking, [(user, EVENT_ID, seat.seat_id) for user in users])

        from boxoffice.repositories import SeatRepository

        with db.read() as connection:
            final = SeatRepository(connection).get(seat.seat_id)
        assert final.status == "booked", "seat should be booked after the race"
        assert final.booked_by_user_id in users, "owner must be one of the contenders"
        assert final.booked_at, "a booked seat must record when it was taken"


def test_every_attempt_is_recorded():
    """The losers are in the audit log too, which is where a race is visible."""
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        seat = available_seats(db, EVENT_ID)[0]
        users = user_ids(db, CONCURRENT_USERS)

        _book_together(booking, [(user, EVENT_ID, seat.seat_id) for user in users])

        with db.read() as connection:
            entries = BookingLogRepository(connection).entries_after(0)
        for_seat = [entry for entry in entries if entry["seat_id"] == seat.seat_id]
        results = Counter(entry["result"] for entry in for_seat)

        assert len(for_seat) == CONCURRENT_USERS, (
            f"expected {CONCURRENT_USERS} log entries, found {len(for_seat)}"
        )
        assert results["booking_successful"] == 1
        assert results["seat_already_booked"] == CONCURRENT_USERS - 1


def _book_together(booking: BookingService, requests: list[tuple[str, str, str]]) -> list[str]:
    """Fire every booking from its own thread, all released at the same instant."""
    outcomes: list[str | None] = [None] * len(requests)
    barrier = threading.Barrier(len(requests))

    def attempt(index: int, user_id: str, event_id: str, seat_id: str) -> None:
        barrier.wait()
        try:
            outcomes[index] = booking.book_seat(
                user_id=user_id, event_id=event_id, seat_id=seat_id
            ).status
        except BoxOfficeError as error:
            outcomes[index] = error.details.get("result", error.code)

    threads = [
        threading.Thread(target=attempt, args=(index, *request))
        for index, request in enumerate(requests)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(outcome is not None for outcome in outcomes), "a booking thread hung"
    return outcomes


if __name__ == "__main__":
    raise SystemExit(run_tests(dict(globals())))
