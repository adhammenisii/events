"""Create the database schema and load it from the Part 1 CSV extracts.

Seeding is idempotent. Re-running it refreshes the static columns (an event
venue, a seat price) but never touches live booking state, so a re-seed after
a data correction does not silently cancel real bookings.

Run standalone:

    python -m boxoffice.db.bootstrap --database instance/boxoffice.db --seed-data data
"""

import argparse
import csv
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..errors import BoxOfficeError
from ..passwords import hash_password
from ..repositories import EventRepository, SeatRepository, UserRepository
from .connection import Database

logger = logging.getLogger(__name__)

# Accounts that can be logged into out of the box. Hashing is deliberately
# expensive, so only a handful of the seeded users get credentials -- enough
# to demonstrate two people booking against each other, without adding half a
# minute to every fresh setup.
DEFAULT_DEMO_ACCOUNTS = 12
DEFAULT_DEMO_PASSWORD = "demo1234"

# Every seat has to be claimable by a distinct account, so the roster is kept
# larger than the venue: more users than there are seats to sell. The sample
# extract from Part 1 carries 200 users against 2,550 seats, so the shortfall
# is made up with generated accounts. The margin is headroom above the seat
# count, not a target in itself.
USER_HEADROOM = 50

# Drawn from the same pools as the Part 1 extract, so a generated account is
# indistinguishable from a seeded one in the interface.
FIRST_NAMES = (
    "Ahmed", "Ali", "Emma", "Hana", "James", "Karim", "Layla", "Liam",
    "Mary", "Mona", "Noah", "Nour", "Olivia", "Omar", "Sara", "Youssef",
)
LAST_NAMES = (
    "Ali", "Brown", "Davis", "Farouk", "Hassan", "Ibrahim",
    "Johnson", "Miller", "Mostafa", "Saleh", "Smith", "Williams",
)

EVENT_COLUMNS = {
    "event_id", "name", "category", "venue", "city",
    "event_date", "event_time", "total_seats", "base_price",
}
USER_COLUMNS = {"user_id", "full_name", "email"}
SEAT_COLUMNS = {"seat_id", "event_id", "section", "row", "seat_number", "price", "status"}


class SeedDataError(BoxOfficeError):
    """A seed CSV is missing, unreadable, or short of required columns."""

    code = "seed_data_invalid"
    status = 500


@dataclass
class SeedReport:
    events: int = 0
    users: int = 0
    seats: int = 0
    generated_users: int = 0
    demo_accounts: list[str] = field(default_factory=list)

    @property
    def total_users(self) -> int:
        return self.users + self.generated_users

    def __str__(self) -> str:
        generated = f" + {self.generated_users} generated" if self.generated_users else ""
        return (
            f"{self.events} events, {self.seats} seats, "
            f"{self.total_users} users ({self.users} from CSV{generated}, "
            f"{len(self.demo_accounts)} with login credentials)"
        )


def ensure_ready(db: Database, seed_dir: Path, **seed_options) -> SeedReport | None:
    """Apply the schema, and seed from CSV only when the database is empty.

    The application calls this at startup, so a first run needs no separate
    setup step and every later run costs one COUNT(*).
    """
    db.apply_schema()
    if not db.is_seeded():
        logger.info("Empty database at %s -- seeding from %s", db.path, seed_dir)
        return seed_from_csv(db, seed_dir, **seed_options)

    # Already populated, but the roster invariant still has to hold: a
    # database seeded before this rule existed, or one whose venue has grown,
    # would otherwise have fewer accounts than seats.
    top_up_users(db, headroom=seed_options.get("user_headroom", USER_HEADROOM))
    return None


def seed_from_csv(
    db: Database,
    seed_dir: Path,
    *,
    demo_accounts: int = DEFAULT_DEMO_ACCOUNTS,
    demo_password: str = DEFAULT_DEMO_PASSWORD,
    user_headroom: int = USER_HEADROOM,
) -> SeedReport:
    seed_dir = Path(seed_dir)
    events = _read_events(seed_dir / "events.csv")
    users = _read_users(seed_dir / "users.csv")
    seats = _read_seats(
        seed_dir / "seats.csv",
        known_events={event["event_id"] for event in events},
        known_users={user["user_id"] for user in users},
    )

    # One transaction for the whole load: a half-seeded database, with seats
    # referencing events that were never inserted, is worse than no database.
    with db.write() as connection:
        EventRepository(connection).upsert_many(events)
        UserRepository(connection).upsert_many(users)
        SeatRepository(connection).upsert_many(seats)

    report = SeedReport(
        events=len(events),
        users=len(users),
        seats=len(seats),
        generated_users=top_up_users(db, headroom=user_headroom),
        demo_accounts=_grant_demo_logins(db, seats, demo_accounts, demo_password),
    )
    logger.info("Seeded: %s", report)
    return report


def _grant_demo_logins(db: Database, seats: list[dict], count: int, password: str) -> list[str]:
    """Give login credentials to the users who already hold the most seats.

    Picking busy accounts rather than the first few by id means the first
    login lands on a seat map with visible bookings of your own, which is the
    state actually worth demonstrating.
    """
    if count <= 0:
        return []

    holdings = Counter(seat["booked_by_user_id"] for seat in seats if seat["booked_by_user_id"])
    granted = []
    with db.write() as connection:
        users = UserRepository(connection)
        for user_id, _ in holdings.most_common(count):
            password_hash, salt = hash_password(password)
            users.set_password(user_id, password_hash, salt)
            granted.append(user_id)
    return granted


def top_up_users(db: Database, *, headroom: int = USER_HEADROOM) -> int:
    """Generate accounts until the roster outnumbers the seats.

    The bound is total seats rather than *available* seats on purpose:
    availability moves every time somebody books or cancels, so testing
    against it would make the invariant hold or fail depending on the hour.
    More users than seats implies more users than available seats at every
    moment, and more users than any single event can hold.

    Idempotent -- it inserts only the shortfall, so running it twice adds
    nothing the second time.
    """
    with db.read() as connection:
        seat_count = connection.execute("SELECT COUNT(*) AS n FROM seats").fetchone()["n"]
        user_count = connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]

    target = seat_count + headroom
    shortfall = target - user_count
    if shortfall <= 0:
        logger.debug("Roster already sufficient: %d users for %d seats.", user_count, seat_count)
        return 0

    with db.write() as connection:
        users = UserRepository(connection)
        starting_id = int(users.next_user_id()[3:])
        generated = [_generated_user(starting_id + offset) for offset in range(shortfall)]
        users.upsert_many(generated)

    logger.info(
        "Added %d generated users so the roster (%d) exceeds the %d seats on sale.",
        shortfall, user_count + shortfall, seat_count,
    )
    return shortfall


def _generated_user(sequence: int) -> dict:
    """Build one account deterministically from its sequence number.

    Deterministic rather than random so that two machines seeding the same
    data end up with the same roster, and so a re-run is a no-op.
    """
    first = FIRST_NAMES[sequence % len(FIRST_NAMES)]
    last = LAST_NAMES[(sequence // len(FIRST_NAMES)) % len(LAST_NAMES)]
    return {
        "user_id": "USR%05d" % sequence,
        "full_name": f"{first} {last}",
        "email": f"{first}.{last}{sequence}@example.com".lower(),
        "phone": "+1-555-%03d-%04d" % (sequence // 10000 % 1000, sequence % 10000),
        "signup_date": date.today().isoformat(),
    }


def _read_csv(path: Path, required: set[str]) -> list[dict]:
    if not path.exists():
        raise SeedDataError(f"Seed file not found: {path}")
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise SeedDataError(
                    f"{path.name} is missing required column(s): {', '.join(sorted(missing))}"
                )
            return list(reader)
    except OSError as exc:
        raise SeedDataError(f"Could not read {path}: {exc}") from exc


def _read_events(path: Path) -> list[dict]:
    return [
        {
            "event_id": row["event_id"].strip(),
            "name": row["name"].strip(),
            "category": row["category"].strip(),
            "venue": row["venue"].strip(),
            "city": row["city"].strip(),
            "event_date": row["event_date"].strip(),
            "event_time": row["event_time"].strip(),
            "total_seats": _as_int(row["total_seats"], path, row["event_id"], "total_seats"),
            "base_price": _as_float(row["base_price"], path, row["event_id"], "base_price"),
        }
        for row in _read_csv(path, EVENT_COLUMNS)
    ]


def _read_users(path: Path) -> list[dict]:
    today = date.today().isoformat()
    return [
        {
            "user_id": row["user_id"].strip(),
            "full_name": row["full_name"].strip(),
            "email": row["email"].strip(),
            "phone": (row.get("phone") or "").strip(),
            "signup_date": (row.get("signup_date") or today).strip(),
        }
        for row in _read_csv(path, USER_COLUMNS)
    ]


def _read_seats(path: Path, *, known_events: set[str], known_users: set[str]) -> list[dict]:
    """Read the seat layout, dropping rows that would break referential integrity.

    A seat pointing at an event that was never exported is a defect in the
    upstream extract, not a reason to abandon the whole load -- such rows are
    logged and skipped so the rest of the venue still arrives intact.
    """
    seats: list[dict] = []
    orphan_events: set[str] = set()
    unknown_owners = 0

    for row in _read_csv(path, SEAT_COLUMNS):
        event_id = row["event_id"].strip()
        if event_id not in known_events:
            orphan_events.add(event_id)
            continue

        owner = (row.get("booked_by_user_id") or "").strip() or None
        if owner and owner not in known_users:
            # Keep the seat, drop the phantom owner: an unknown user id would
            # violate the foreign key and take the whole transaction with it.
            owner = None
            unknown_owners += 1

        status = row["status"].strip().lower()
        if status not in {"available", "booked"} or (status == "booked" and owner is None):
            # The schema requires status and owner to agree; the CSV does not
            # guarantee it, so an unusable pair falls back to available.
            status = "available"
            owner = None

        seat_id = row["seat_id"].strip()
        seats.append(
            {
                "seat_id": seat_id,
                "event_id": event_id,
                "section": row["section"].strip(),
                "row_label": row["row"].strip(),
                "seat_number": _as_int(row["seat_number"], path, seat_id, "seat_number"),
                "price": _as_float(row["price"], path, seat_id, "price"),
                "status": status,
                "booked_by_user_id": owner,
                "booked_at": None,
            }
        )

    if orphan_events:
        logger.warning(
            "Skipped seats for %d unknown event(s): %s",
            len(orphan_events),
            ", ".join(sorted(orphan_events)[:5]),
        )
    if unknown_owners:
        logger.warning("Released %d seat(s) held by users missing from users.csv.", unknown_owners)
    return seats


def _as_int(value: str, path: Path, row_id: str, column: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise SeedDataError(
            f"{path.name}: row {row_id} has a non-numeric {column}: {value!r}"
        ) from exc


def _as_float(value: str, path: Path, row_id: str, column: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SeedDataError(
            f"{path.name}: row {row_id} has a non-numeric {column}: {value!r}"
        ) from exc


def main() -> int:
    from ..config import AppConfig

    defaults = AppConfig.from_environment()
    parser = argparse.ArgumentParser(description="Create and seed the Box Office database.")
    parser.add_argument("--database", type=Path, default=defaults.database_path)
    parser.add_argument("--seed-data", type=Path, default=defaults.seed_data_dir)
    parser.add_argument("--demo-accounts", type=int, default=DEFAULT_DEMO_ACCOUNTS,
                        help="How many seeded users are given login credentials.")
    parser.add_argument("--demo-password", default=DEFAULT_DEMO_PASSWORD)
    parser.add_argument("--user-headroom", type=int, default=USER_HEADROOM,
                        help="How many more accounts than seats the roster should hold.")
    parser.add_argument("--reset", action="store_true",
                        help="Delete the existing database file before seeding.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    if args.reset:
        # WAL mode keeps two sidecar files; leaving them behind would attach
        # the old journal to the new database.
        for suffix in ("", "-wal", "-shm"):
            Path(str(args.database) + suffix).unlink(missing_ok=True)
        logger.info("Removed any existing database at %s", args.database)

    db = Database(args.database)
    try:
        db.apply_schema()
        report = seed_from_csv(
            db,
            args.seed_data,
            demo_accounts=args.demo_accounts,
            demo_password=args.demo_password,
            user_headroom=args.user_headroom,
        )
    except BoxOfficeError as exc:
        logger.error("Seeding failed: %s", exc.message)
        return 1

    print(f"Database ready at {args.database}")
    print(f"  {report}")
    if report.demo_accounts:
        print(f"  Demo accounts sign in with the password: {args.demo_password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
