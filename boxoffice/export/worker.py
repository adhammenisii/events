"""Mirrors committed bookings out to the Part 1 storage layout.

Two things are exported after a change: new rows appended to the audit log,
and a full snapshot of seat status. The snapshot is 2,500-odd rows, and
writing it to HDFS can take seconds -- far too long to do while a customer
waits for a click to resolve.

So the export runs on one background thread and *coalesces*: while a write is
in flight, any number of further bookings collapse into a single pending
request, and one snapshot at the end reflects them all. Bookings are never
blocked by it, and the mirror always converges on current state.
"""

import logging
import threading

from ..db import Database
from ..repositories import BookingLogRepository, SeatRepository
from .targets import ExportTarget, ExportUnavailable

logger = logging.getLogger(__name__)

SHUTDOWN_GRACE_SECONDS = 10


class StorageExporter:
    def __init__(self, db: Database, target: ExportTarget):
        self._db = db
        self._target = target
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._last_exported_entry_id = 0
        self._thread: threading.Thread | None = None
        self._failures = 0

    def start(self) -> None:
        """Begin exporting, catching up on anything logged while we were down."""
        with self._db.read() as connection:
            # Everything already in the log was mirrored by whichever process
            # wrote it; only rows added from now on need exporting.
            self._last_exported_entry_id = BookingLogRepository(connection).latest_entry_id()

        self._thread = threading.Thread(target=self._run, name="storage-export", daemon=True)
        self._thread.start()
        # Write one snapshot straight away. Without it the mirror stays at
        # whatever the last run left behind until somebody books something.
        self.request_export()

    def request_export(self) -> None:
        """Ask for a mirror pass. Cheap, non-blocking, safe to call per booking."""
        self._wake.set()

    def stop(self) -> None:
        """Stop the worker after letting any pending export finish."""
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=SHUTDOWN_GRACE_SECONDS)
            if self._thread.is_alive():
                logger.warning("Storage export did not stop within %ds.", SHUTDOWN_GRACE_SECONDS)

    def export_now(self) -> None:
        """Run one pass synchronously. Used by tests and the CLI."""
        self._export_once()

    def _run(self) -> None:
        while not self._stopping.is_set():
            self._wake.wait()
            # Cleared before the export, not after: a booking that lands while
            # this pass is running sets the flag again and gets its own pass,
            # instead of being silently absorbed and never mirrored.
            self._wake.clear()
            try:
                self._export_once()
            except ExportUnavailable as exc:
                self._note_failure(exc)
            except Exception:
                logger.exception("Unexpected failure in the storage export thread.")

    def _export_once(self) -> None:
        with self._db.read() as connection:
            new_entries = BookingLogRepository(connection).entries_after(
                self._last_exported_entry_id
            )
            seats = SeatRepository(connection).list_all()

        if new_entries:
            self._target.append_log_rows(
                [
                    [
                        entry["created_at"], entry["user_id"], entry["event_id"],
                        entry["seat_id"], entry["action"], entry["result"], entry["message"],
                    ]
                    for entry in new_entries
                ]
            )
            # Advanced only after the append succeeds, so a failed export is
            # retried on the next pass rather than skipped.
            self._last_exported_entry_id = new_entries[-1]["entry_id"]

        self._target.replace_seats_snapshot(
            [
                [
                    seat.seat_id, seat.event_id, seat.section, seat.row_label,
                    seat.seat_number, seat.price, seat.status, seat.booked_by_user_id or "",
                ]
                for seat in seats
            ]
        )
        if self._failures:
            logger.info("Storage export recovered after %d failure(s).", self._failures)
            self._failures = 0

    def _note_failure(self, exc: ExportUnavailable) -> None:
        """Log the first failure loudly, then stay quiet until it recovers.

        A cluster that is down stays down for a while; repeating the same
        error for every booking would bury everything else in the log.
        """
        self._failures += 1
        if self._failures == 1:
            logger.error("Storage export failed: %s", exc)
        elif self._failures % 50 == 0:
            logger.error("Storage export still failing after %d attempts: %s", self._failures, exc)
