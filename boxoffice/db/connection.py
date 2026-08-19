"""SQLite connection management.

SQLite is a perfectly good fit here -- a single-file database, no server to
administer, and (in WAL mode) readers that never block the writer. The two
things it needs to be told are: use WAL, and wait rather than fail when two
writers collide. Both are set on every connection below.

Connections are deliberately *not* cached in a module-level global. They are
cheap to open against an existing file, and a per-use connection removes the
whole class of bugs that comes from sharing one handle between threads.
"""

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..errors import StorageError

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# How long a writer waits for a competing write transaction before giving up.
# Comfortably longer than any transaction this application runs, so a busy
# timeout in practice means something is genuinely wrong.
BUSY_TIMEOUT_MS = 10_000


class Database:
    """Owns the database file and hands out configured connections."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            timeout=BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,  # transactions are managed explicitly below
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        return connection

    @contextmanager
    def read(self):
        """A connection for queries that do not modify anything."""
        connection = self._open()
        try:
            yield connection
        except sqlite3.Error as exc:
            raise _as_storage_error(exc) from exc
        finally:
            connection.close()

    @contextmanager
    def write(self):
        """A connection inside an immediate transaction.

        BEGIN IMMEDIATE takes the write lock up front instead of upgrading
        halfway through. That turns "two writers raced" into a short wait at
        the start rather than a SQLITE_BUSY thrown after the caller already
        believes it holds the row.
        """
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
        except sqlite3.Error as exc:
            _rollback_quietly(connection)
            raise _as_storage_error(exc) from exc
        except Exception:
            _rollback_quietly(connection)
            raise
        else:
            connection.execute("COMMIT")
        finally:
            connection.close()

    def apply_schema(self) -> None:
        """Create any missing tables and indexes. Safe to run repeatedly."""
        script = SCHEMA_PATH.read_text(encoding="utf-8")
        connection = self._open()
        try:
            connection.executescript(script)
        except sqlite3.Error as exc:
            raise StorageError(f"Could not initialise the database schema: {exc}") from exc
        finally:
            connection.close()

    def check_ready(self) -> None:
        """Cheap round trip used by the health endpoint."""
        with self.read() as connection:
            connection.execute("SELECT 1").fetchone()

    def is_seeded(self) -> bool:
        with self.read() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        return row["n"] > 0


def _rollback_quietly(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        # The transaction is already gone (rolled back by SQLite itself, or
        # never opened). Nothing left to undo, and raising here would mask
        # the original failure.
        logger.debug("Rollback skipped; no active transaction.", exc_info=True)


def _as_storage_error(exc: sqlite3.Error) -> StorageError:
    """Translate a driver-level failure into something a caller can show.

    An IntegrityError means the caller sent data the schema rejects and is
    worth surfacing verbatim; everything else (locked, corrupt, disk full) is
    an operational problem the user can only retry.
    """
    logger.error("Database error: %s", exc, exc_info=True)
    if isinstance(exc, sqlite3.IntegrityError):
        return StorageError(f"The database rejected this change: {exc}")
    return StorageError("The booking database is temporarily unavailable. Please try again.")
