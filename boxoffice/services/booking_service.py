"""Write-side business logic: booking and cancelling seats.

The concurrency guarantee itself lives one layer down, in
:meth:`SeatRepository.claim` -- a conditional UPDATE that only matches a row
still marked available. This service owns everything around it: validating
the request, deciding which error a failed claim represents, recording the
attempt whether it succeeded or not, and returning fresh statistics alongside
the result.
"""

import logging
from dataclasses import dataclass

from ..clock import utc_now_iso
from ..db import Database
from ..errors import (
    CancelRejectedError,
    SeatNotFoundError,
    SeatUnavailableError,
    ValidationError,
)
from ..models import EventStats, Seat
from ..repositories import BookingLogRepository, EventRepository, SeatRepository, UserRepository

logger = logging.getLogger(__name__)

BOOKING_CONFIRMED = "booking_successful"
BOOKING_CANCELLED = "booking_cancelled"
SEAT_TAKEN = "seat_already_booked"
SEAT_UNKNOWN = "seat_unavailable"
CANCEL_REJECTED = "cancel_failed"

_ERROR_FOR_RESULT = {
    SEAT_TAKEN: SeatUnavailableError,
    CANCEL_REJECTED: CancelRejectedError,
    SEAT_UNKNOWN: SeatNotFoundError,
}


@dataclass(frozen=True, slots=True)
class BookingOutcome:
    """A completed booking or cancellation, plus the state it produced.

    Returning the updated seat *and* the event statistics lets the UI apply
    both from a single response, rather than re-fetching the whole seat map
    and hoping the numbers still describe what it just drew.
    """

    status: str
    message: str
    seat: Seat
    stats: EventStats

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "message": self.message,
            "seat": self.seat.to_dict(),
            "stats": self.stats.to_dict(),
        }


class _Rejected(Exception):
    """Internal signal: abandon the booking transaction with a reason.

    Raising unwinds the transaction, so nothing half-written survives. The
    audit entry is then written by :meth:`BookingService._reject` on a fresh
    transaction -- a refusal is exactly the kind of event the log exists to
    keep, and it would be rolled away if recorded inline.
    """

    def __init__(self, *, action: str, result: str, message: str, event_id: str):
        super().__init__(message)
        self.action = action
        self.result = result
        self.message = message
        self.event_id = event_id


class BookingService:
    def __init__(self, db: Database, on_change=None):
        """``on_change`` runs after a committed change, for the storage export.

        It is invoked outside the transaction and its failures are contained:
        mirroring state to the Part 1 cluster is a downstream concern, and a
        broken mirror must never undo a booking the customer was told about.
        """
        self._db = db
        self._on_change = on_change

    def book_seat(self, *, user_id: str, event_id: str, seat_id: str) -> BookingOutcome:
        _require_identifiers(user_id=user_id, event_id=event_id, seat_id=seat_id)
        try:
            with self._db.write() as connection:
                seats = SeatRepository(connection)

                seat = seats.get(seat_id)
                if seat is None or seat.event_id != event_id:
                    raise _Rejected(action="book", result=SEAT_UNKNOWN, event_id=event_id,
                                    message="That seat is not part of this event.")

                if not UserRepository(connection).exists(user_id):
                    # A session whose account was removed mid-flight. Caught
                    # here so it reads as a rejected booking rather than a
                    # foreign-key crash.
                    raise _Rejected(action="book", result=SEAT_UNKNOWN, event_id=event_id,
                                    message="This account no longer exists.")

                if not seats.claim(seat_id, user_id, utc_now_iso()):
                    current = seats.get(seat_id)
                    held_by_caller = current is not None and current.booked_by_user_id == user_id
                    raise _Rejected(
                        action="book", result=SEAT_TAKEN, event_id=event_id,
                        message="You already hold this seat." if held_by_caller
                        else "Someone else booked this seat first.",
                    )

                message = "Seat booked."
                BookingLogRepository(connection).record(
                    created_at=utc_now_iso(), user_id=user_id, event_id=event_id,
                    seat_id=seat_id, action="book", result=BOOKING_CONFIRMED, message=message,
                )
                outcome = BookingOutcome(
                    status=BOOKING_CONFIRMED,
                    message=message,
                    seat=seats.get(seat_id),
                    stats=EventRepository(connection).stats(event_id),
                )
        except _Rejected as rejection:
            raise self._reject(rejection, user_id=user_id, seat_id=seat_id) from None

        self._notify_change()
        logger.info("Seat %s booked by %s", seat_id, user_id)
        return outcome

    def cancel_booking(self, *, user_id: str, seat_id: str) -> BookingOutcome:
        _require_identifiers(user_id=user_id, seat_id=seat_id)
        try:
            with self._db.write() as connection:
                seats = SeatRepository(connection)

                seat = seats.get(seat_id)
                if seat is None:
                    raise _Rejected(action="cancel", result=SEAT_UNKNOWN, event_id="",
                                    message="That seat does not exist.")

                if not seats.release(seat_id, user_id):
                    raise _Rejected(
                        action="cancel", result=CANCEL_REJECTED, event_id=seat.event_id,
                        message="That seat is booked by someone else." if seat.is_booked
                        else "That seat is not currently booked.",
                    )

                message = "Booking cancelled."
                BookingLogRepository(connection).record(
                    created_at=utc_now_iso(), user_id=user_id, event_id=seat.event_id,
                    seat_id=seat_id, action="cancel", result=BOOKING_CANCELLED, message=message,
                )
                outcome = BookingOutcome(
                    status=BOOKING_CANCELLED,
                    message=message,
                    seat=seats.get(seat_id),
                    stats=EventRepository(connection).stats(seat.event_id),
                )
        except _Rejected as rejection:
            raise self._reject(rejection, user_id=user_id, seat_id=seat_id) from None

        self._notify_change()
        logger.info("Seat %s released by %s", seat_id, user_id)
        return outcome

    def history_for_user(self, user_id: str, limit: int = 20) -> list[dict]:
        with self._db.read() as connection:
            return BookingLogRepository(connection).recent_for_user(user_id, limit)

    def _reject(self, rejection: _Rejected, *, user_id: str, seat_id: str):
        """Persist a refused attempt and build the error to raise for it."""
        try:
            with self._db.write() as connection:
                BookingLogRepository(connection).record(
                    created_at=utc_now_iso(), user_id=user_id, event_id=rejection.event_id,
                    seat_id=seat_id, action=rejection.action, result=rejection.result,
                    message=rejection.message,
                )
        except Exception:
            # The customer still needs their answer; losing one audit row is
            # preferable to turning a clean rejection into a 500.
            logger.warning("Could not record rejected %s of %s", rejection.action, seat_id,
                           exc_info=True)

        error_type = _ERROR_FOR_RESULT.get(rejection.result, SeatNotFoundError)
        return error_type(
            rejection.message, details={"result": rejection.result, "seat_id": seat_id}
        )

    def _notify_change(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:
            logger.warning("Storage export hook failed after a booking change.", exc_info=True)


def _require_identifiers(**identifiers: str) -> None:
    """Reject blank or non-string identifiers before they ever reach SQL."""
    for name, value in identifiers.items():
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{name} is required.", details={"field": name})
