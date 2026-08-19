"""The seat you asked for is the seat you get -- or nothing at all.

Two failure modes matter here, and neither is theoretical in a booking
system: silently allocating a *different* seat when the requested one is
gone, and letting two people hold the same one. These tests pin down both by
comparing the whole event before and after each request, rather than only
inspecting the seat under test.
"""

from support import TemporaryDatabase, available_seats, run_tests, user_ids

from boxoffice.errors import SeatNotFoundError, SeatUnavailableError, ValidationError
from boxoffice.repositories import EventRepository, SeatRepository
from boxoffice.services import BookingService

EVENT_ID = "EVT00002"

# Parameterised queries make this inert; the test keeps it that way.
INJECTION_PROBE = "'; UPDATE seats SET status='booked'; --"


def _seat_states(db, event_id: str = EVENT_ID) -> dict:
    """Status and owner of every seat in an event, keyed by seat id."""
    with db.read() as connection:
        return {
            seat.seat_id: (seat.status, seat.booked_by_user_id)
            for seat in SeatRepository(connection).list_for_event(event_id)
        }


def _differences(before: dict, after: dict) -> dict:
    return {seat_id: (before[seat_id], after[seat_id])
            for seat_id in before if before[seat_id] != after[seat_id]}


def test_booking_changes_exactly_the_requested_seat_and_nothing_else():
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        target = available_seats(db, EVENT_ID, 40)[17]   # deliberately not the first free seat
        user = user_ids(db, 1)[0]

        before = _seat_states(db)
        outcome = booking.book_seat(user_id=user, event_id=EVENT_ID, seat_id=target.seat_id)
        after = _seat_states(db)

        changed = _differences(before, after)
        assert list(changed) == [target.seat_id], (
            f"expected only {target.seat_id} to change, but these did: {list(changed)}"
        )
        assert after[target.seat_id] == ("booked", user)
        assert outcome.seat.seat_id == target.seat_id, "the response must describe the same seat"


def test_booking_does_not_drift_to_a_neighbouring_seat():
    """Seat ids here share long prefixes, so an off-by-one would otherwise hide."""
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        neighbours = available_seats(db, EVENT_ID, 3)
        target = neighbours[1]
        user = user_ids(db, 1)[0]

        booking.book_seat(user_id=user, event_id=EVENT_ID, seat_id=target.seat_id)
        states = _seat_states(db)

        assert states[target.seat_id][0] == "booked"
        assert states[neighbours[0].seat_id][0] == "available", "the seat before it moved"
        assert states[neighbours[2].seat_id][0] == "available", "the seat after it moved"


def test_every_seat_id_in_a_batch_lands_on_its_own_seat():
    """Book twenty specific seats; each must end up exactly where it was asked."""
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        targets = available_seats(db, EVENT_ID, 20)
        bookers = user_ids(db, 20)

        for user, seat in zip(bookers, targets):
            outcome = booking.book_seat(user_id=user, event_id=EVENT_ID, seat_id=seat.seat_id)
            assert outcome.seat.seat_id == seat.seat_id

        states = _seat_states(db)
        for user, seat in zip(bookers, targets):
            assert states[seat.seat_id] == ("booked", user), (
                f"{seat.seat_id} should belong to {user}, found {states[seat.seat_id]}"
            )


def test_a_booked_seat_is_rejected_and_no_substitute_is_allocated():
    """The heart of the requirement: refuse, do not quietly book something else."""
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        target = available_seats(db, EVENT_ID)[0]
        owner, latecomer = user_ids(db, 2)

        booking.book_seat(user_id=owner, event_id=EVENT_ID, seat_id=target.seat_id)
        before = _seat_states(db)

        try:
            booking.book_seat(user_id=latecomer, event_id=EVENT_ID, seat_id=target.seat_id)
            raise AssertionError("booking an occupied seat should have been refused")
        except SeatUnavailableError as error:
            assert error.status == 409
            assert error.code == "seat_already_booked"
            assert error.details["seat_id"] == target.seat_id

        after = _seat_states(db)
        assert _differences(before, after) == {}, (
            "a refused booking must leave every seat exactly as it was"
        )
        assert after[target.seat_id] == ("booked", owner), "the original holder keeps the seat"

        # The latecomer already holds seats in the sample data, so "no
        # substitute" means their holdings are unchanged -- not that they are
        # empty.
        held_before = {s for s, (_, who) in before.items() if who == latecomer}
        held_after = {s for s, (_, who) in after.items() if who == latecomer}
        assert held_after == held_before, (
            f"the rejected booker gained {held_after - held_before}"
        )


def test_the_rejection_message_says_the_seat_is_taken():
    """The message reaches the customer verbatim, so it has to read as English."""
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        target = available_seats(db, EVENT_ID)[0]
        owner, latecomer = user_ids(db, 2)
        booking.book_seat(user_id=owner, event_id=EVENT_ID, seat_id=target.seat_id)

        message = None
        try:
            booking.book_seat(user_id=latecomer, event_id=EVENT_ID, seat_id=target.seat_id)
        except SeatUnavailableError as error:
            message = error.message
        assert message, "a refusal must carry a message"
        assert "booked" in message.lower(), f"unhelpful rejection message: {message!r}"
        assert message.endswith("."), "messages are shown as-is and should be sentences"

        # Re-booking a seat you already hold gets its own wording, not this one.
        try:
            booking.book_seat(user_id=owner, event_id=EVENT_ID, seat_id=target.seat_id)
        except SeatUnavailableError as error:
            assert error.message != message
            assert "already hold" in error.message.lower()


def test_statistics_do_not_move_when_a_booking_is_refused():
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        target = available_seats(db, EVENT_ID)[0]
        owner, latecomer = user_ids(db, 2)
        booking.book_seat(user_id=owner, event_id=EVENT_ID, seat_id=target.seat_id)

        with db.read() as connection:
            before = EventRepository(connection).stats(EVENT_ID)
        try:
            booking.book_seat(user_id=latecomer, event_id=EVENT_ID, seat_id=target.seat_id)
        except SeatUnavailableError:
            pass
        with db.read() as connection:
            after = EventRepository(connection).stats(EVENT_ID)

        assert (after.booked_seats, after.available_seats) == (
            before.booked_seats, before.available_seats
        )
        assert abs(after.revenue - before.revenue) < 0.01


def test_a_seat_id_from_another_event_is_refused_not_remapped():
    """A seat id is global. Naming it under the wrong event must not book the
    equivalent section and row inside the event that was named."""
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        foreign = available_seats(db, "EVT00006")[0]
        user = user_ids(db, 1)[0]

        before = _seat_states(db)
        try:
            booking.book_seat(user_id=user, event_id=EVENT_ID, seat_id=foreign.seat_id)
            raise AssertionError("a seat from another event should have been refused")
        except SeatNotFoundError as error:
            assert error.status == 404

        assert _differences(before, _seat_states(db)) == {}
        assert _seat_states(db, "EVT00006")[foreign.seat_id][0] == "available", (
            "the seat actually named in the request must not have been booked either"
        )


def test_an_unusable_seat_id_books_nothing():
    """Nonsense ids are refused outright -- including one shaped like an injection."""
    with TemporaryDatabase() as db:
        booking = BookingService(db)
        user = user_ids(db, 1)[0]
        before = _seat_states(db)

        for made_up in ("EVT00002-VIP-99999", "", "   ", INJECTION_PROBE):
            try:
                booking.book_seat(user_id=user, event_id=EVENT_ID, seat_id=made_up)
                raise AssertionError(f"{made_up!r} should not have booked anything")
            except (SeatNotFoundError, ValidationError):
                pass

        assert _differences(before, _seat_states(db)) == {}


if __name__ == "__main__":
    raise SystemExit(run_tests(dict(globals())))
