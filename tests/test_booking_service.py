"""Booking lifecycle and every way a booking can be refused."""

from support import TemporaryDatabase, available_seats, run_tests, user_ids

from boxoffice.errors import (
    CancelRejectedError,
    SeatNotFoundError,
    SeatUnavailableError,
    ValidationError,
)
from boxoffice.repositories import SeatRepository
from boxoffice.services import BookingService, CatalogService

EVENT_ID = "EVT00005"


def test_booking_marks_the_seat_and_returns_fresh_statistics():
    with TemporaryDatabase() as db:
        booking, catalog = BookingService(db), CatalogService(db)
        seat = available_seats(db, EVENT_ID)[0]
        user = user_ids(db, 1)[0]
        before = catalog.event_stats(EVENT_ID)

        outcome = booking.book_seat(user_id=user, event_id=EVENT_ID, seat_id=seat.seat_id)

        assert outcome.status == "booking_successful"
        assert outcome.seat.status == "booked"
        assert outcome.seat.booked_by_user_id == user
        assert outcome.stats.booked_seats == before.booked_seats + 1
        assert outcome.stats.available_seats == before.available_seats - 1
        assert abs(outcome.stats.revenue - (before.revenue + seat.price)) < 0.01


def test_cancelling_returns_the_seat_to_the_pool():
    with TemporaryDatabase() as db:
        booking, catalog = BookingService(db), CatalogService(db)
        seat = available_seats(db, EVENT_ID)[0]
        user = user_ids(db, 1)[0]
        before = catalog.event_stats(EVENT_ID)

        booking.book_seat(user_id=user, event_id=EVENT_ID, seat_id=seat.seat_id)
        outcome = booking.cancel_booking(user_id=user, seat_id=seat.seat_id)

        assert outcome.status == "booking_cancelled"
        assert outcome.seat.status == "available"
        assert outcome.seat.booked_by_user_id is None
        assert outcome.stats.booked_seats == before.booked_seats
        assert abs(outcome.stats.revenue - before.revenue) < 0.01


def test_a_taken_seat_is_refused():
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        seat = available_seats(db, EVENT_ID)[0]
        first, second = user_ids(db, 2)

        booking.book_seat(user_id=first, event_id=EVENT_ID, seat_id=seat.seat_id)
        error = _expect(SeatUnavailableError, booking.book_seat,
                        user_id=second, event_id=EVENT_ID, seat_id=seat.seat_id)

        assert error.code == "seat_already_booked"
        assert error.status == 409
        with db.read() as connection:
            assert SeatRepository(connection).get(seat.seat_id).booked_by_user_id == first


def test_a_seat_from_another_event_is_refused():
    """Guards against a client sending a seat id that belongs elsewhere."""
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        seat = available_seats(db, EVENT_ID)[0]
        user = user_ids(db, 1)[0]

        error = _expect(SeatNotFoundError, booking.book_seat,
                        user_id=user, event_id="EVT00009", seat_id=seat.seat_id)
        assert error.status == 404


def test_unknown_seat_and_user_are_refused():
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        user = user_ids(db, 1)[0]
        seat = available_seats(db, EVENT_ID)[0]

        _expect(SeatNotFoundError, booking.book_seat,
                user_id=user, event_id=EVENT_ID, seat_id="EVT00005-NOPE-1")
        _expect(SeatNotFoundError, booking.book_seat,
                user_id="USR99999", event_id=EVENT_ID, seat_id=seat.seat_id)


def test_blank_identifiers_are_rejected_before_reaching_the_database():
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        for kwargs in (
            {"user_id": "", "event_id": EVENT_ID, "seat_id": "x"},
            {"user_id": "USR00001", "event_id": "   ", "seat_id": "x"},
            {"user_id": "USR00001", "event_id": EVENT_ID, "seat_id": None},
        ):
            error = _expect(ValidationError, booking.book_seat, **kwargs)
            assert error.status == 400


def test_cancelling_someone_elses_booking_is_refused():
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        seat = available_seats(db, EVENT_ID)[0]
        owner, stranger = user_ids(db, 2)

        booking.book_seat(user_id=owner, event_id=EVENT_ID, seat_id=seat.seat_id)
        error = _expect(CancelRejectedError, booking.cancel_booking,
                        user_id=stranger, seat_id=seat.seat_id)

        assert error.code == "cancel_failed"
        with db.read() as connection:
            assert SeatRepository(connection).get(seat.seat_id).booked_by_user_id == owner


def test_cancelling_a_free_seat_is_refused():
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        seat = available_seats(db, EVENT_ID)[0]
        user = user_ids(db, 1)[0]

        error = _expect(CancelRejectedError, booking.cancel_booking,
                        user_id=user, seat_id=seat.seat_id)
        assert "not currently booked" in error.message


def test_history_includes_refusals():
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        seat = available_seats(db, EVENT_ID)[0]
        owner, stranger = user_ids(db, 2)

        booking.book_seat(user_id=owner, event_id=EVENT_ID, seat_id=seat.seat_id)
        _expect(SeatUnavailableError, booking.book_seat,
                user_id=stranger, event_id=EVENT_ID, seat_id=seat.seat_id)

        history = booking.history_for_user(stranger)
        assert len(history) == 1
        assert history[0]["result"] == "seat_already_booked"


def test_export_hook_failure_does_not_break_a_booking():
    """The mirror is downstream; a broken one must not lose a real booking."""
    with TemporaryDatabase() as db:
        def explode():
            raise RuntimeError("storage is on fire")

        booking = BookingService(db, on_change=explode)
        seat = available_seats(db, EVENT_ID)[0]
        user = user_ids(db, 1)[0]

        outcome = booking.book_seat(user_id=user, event_id=EVENT_ID, seat_id=seat.seat_id)
        assert outcome.status == "booking_successful"
        with db.read() as connection:
            assert SeatRepository(connection).get(seat.seat_id).status == "booked"


def _expect(error_type, call, **kwargs):
    try:
        call(**kwargs)
    except error_type as error:
        return error
    except Exception as unexpected:
        raise AssertionError(
            f"expected {error_type.__name__}, got {type(unexpected).__name__}: {unexpected}"
        ) from None
    raise AssertionError(f"expected {error_type.__name__}, but the call succeeded")


if __name__ == "__main__":
    raise SystemExit(run_tests(dict(globals())))
