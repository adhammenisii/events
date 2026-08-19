"""Shared fixtures for the test suite.

Every test gets its own SQLite file seeded from the sample CSVs, so tests
never interfere with each other or with the database the application uses.
Password hashing is skipped during seeding -- it is deliberately slow, and
only the auth tests need credentials, which they create themselves.
"""

import logging
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from boxoffice.db import Database                       # noqa: E402
from boxoffice.db.bootstrap import seed_from_csv        # noqa: E402

SEED_DATA_DIR = PROJECT_ROOT / "data"


def quiet_logging() -> None:
    """Keep expected warnings out of the test output."""
    logging.disable(logging.WARNING)


class TemporaryDatabase:
    """A seeded database in a temporary directory, cleaned up on exit."""

    def __init__(self, *, demo_accounts: int = 0):
        self.directory = Path(tempfile.mkdtemp(prefix="boxoffice-test-"))
        self.db = Database(self.directory / "test.db")
        self.db.apply_schema()
        self.report = seed_from_csv(self.db, SEED_DATA_DIR, demo_accounts=demo_accounts)

    def __enter__(self) -> Database:
        return self.db

    def __exit__(self, *exception) -> None:
        self.close()

    def close(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


def available_seats(db: Database, event_id: str, count: int = 1) -> list:
    """Pick seats that are free in the seeded data, failing loudly if short."""
    from boxoffice.repositories import SeatRepository

    with db.read() as connection:
        seats = [s for s in SeatRepository(connection).list_for_event(event_id)
                 if s.status == "available"]
    assert len(seats) >= count, (
        f"{event_id} has {len(seats)} available seats, test needs {count}."
    )
    return seats[:count]


def user_ids(db: Database, count: int) -> list[str]:
    with db.read() as connection:
        rows = connection.execute(
            "SELECT user_id FROM users ORDER BY user_id LIMIT ?", (count,)
        ).fetchall()
    assert len(rows) == count, "Sample data does not contain enough users."
    return [row["user_id"] for row in rows]


def run_tests(module_globals: dict) -> int:
    """Run every ``test_*`` function in a module and report the outcome.

    Lets each file stay runnable with a plain ``python tests/test_x.py`` while
    still being an ordinary pytest module.
    """
    quiet_logging()
    tests = sorted(
        (name, fn) for name, fn in module_globals.items()
        if name.startswith("test_") and callable(fn)
    )
    failures = 0
    for name, test in tests:
        try:
            test()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:
            failures += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")

    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0
