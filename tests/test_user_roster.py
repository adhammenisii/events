"""The roster has to outnumber the venue.

Every seat must be claimable by a distinct account, so the number of
registered users is kept above the number of seats on sale. The bound is
*total* seats rather than currently-available ones on purpose: availability
moves with every booking and cancellation, so an invariant written against it
would hold or fail depending on the hour. More users than seats implies more
users than available seats at every moment, and more than any single event
can hold.
"""

from support import TemporaryDatabase, run_tests

from boxoffice.db.bootstrap import USER_HEADROOM, top_up_users
from boxoffice.services import BookingService, CatalogService


def _counts(db) -> dict:
    with db.read() as connection:
        return {
            "users": connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"],
            "seats": connection.execute("SELECT COUNT(*) AS n FROM seats").fetchone()["n"],
            "available": connection.execute(
                "SELECT COUNT(*) AS n FROM seats WHERE status = 'available'"
            ).fetchone()["n"],
            "largest_event": connection.execute(
                "SELECT COUNT(*) AS n FROM seats GROUP BY event_id ORDER BY n DESC LIMIT 1"
            ).fetchone()["n"],
        }


def test_seeding_leaves_more_users_than_seats():
    with TemporaryDatabase() as db:
        counts = _counts(db)
        assert counts["users"] > counts["seats"], (
            f"{counts['users']} users for {counts['seats']} seats -- the roster is short"
        )
        assert counts["users"] >= counts["seats"] + USER_HEADROOM


def test_users_outnumber_available_seats_overall_and_per_event():
    with TemporaryDatabase() as db:
        counts = _counts(db)
        assert counts["users"] > counts["available"], (
            f"{counts['users']} users cannot cover {counts['available']} available seats"
        )
        assert counts["users"] > counts["largest_event"], (
            "the busiest single event must still be coverable by distinct users"
        )


def test_the_invariant_survives_bookings_and_cancellations():
    """Availability moves; the guarantee must not move with it."""
    with TemporaryDatabase() as db:
        booking, catalog = BookingService(db), CatalogService(db)
        _, seats, _ = catalog.seat_map("EVT00004")
        free = [seat for seat in seats if seat.status == "available"][:10]

        for index, seat in enumerate(free):
            booking.book_seat(user_id="USR%05d" % (index + 1), event_id="EVT00004",
                              seat_id=seat.seat_id)
            counts = _counts(db)
            assert counts["users"] > counts["available"]

        for index, seat in enumerate(free):
            booking.cancel_booking(user_id="USR%05d" % (index + 1), seat_id=seat.seat_id)
            counts = _counts(db)
            assert counts["users"] > counts["available"]


def test_generated_accounts_are_usable_for_booking():
    """A padded roster is worthless if the extra accounts cannot book."""
    with TemporaryDatabase() as db:
        booking, catalog = BookingService(db), CatalogService(db)
        with db.read() as connection:
            generated = connection.execute(
                """
                SELECT user_id FROM users
                 WHERE CAST(SUBSTR(user_id, 4) AS INTEGER) > 200
                 ORDER BY user_id LIMIT 1
                """
            ).fetchone()
        assert generated is not None, "no generated accounts were created"

        _, seats, _ = catalog.seat_map("EVT00017")
        seat = next(s for s in seats if s.status == "available")
        outcome = booking.book_seat(
            user_id=generated["user_id"], event_id="EVT00017", seat_id=seat.seat_id
        )
        assert outcome.status == "booking_successful"
        assert outcome.seat.booked_by_user_id == generated["user_id"]


def test_generated_accounts_are_distinct_and_well_formed():
    with TemporaryDatabase() as db:
        with db.read() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)                        AS total,
                       COUNT(DISTINCT user_id)         AS ids,
                       COUNT(DISTINCT LOWER(email))    AS emails,
                       SUM(TRIM(full_name) = '')       AS nameless
                  FROM users
                """
            ).fetchone()

        assert row["ids"] == row["total"], "duplicate user ids"
        assert row["emails"] == row["total"], "duplicate email addresses"
        assert row["nameless"] == 0, "accounts without a name"


def test_topping_up_twice_adds_nothing_the_second_time():
    with TemporaryDatabase() as db:
        before = _counts(db)["users"]
        added = top_up_users(db)
        assert added == 0, f"seeding should already have satisfied the roster, added {added}"
        assert _counts(db)["users"] == before


if __name__ == "__main__":
    raise SystemExit(run_tests(dict(globals())))
