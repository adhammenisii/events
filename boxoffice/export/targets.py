"""Where the exported CSVs are written.

Part 1 of this system defined /ticket_system on HDFS as the durable home for
seat and booking data, and Part 2's batch jobs read from it. The booking
service keeps its own authoritative copy in SQLite and mirrors changes out to
that location, so the two targets below differ only in how bytes are placed:
one writes to a local folder, the other shells out to the hdfs client the
same way Part 1 did.
"""

import csv
import io
import logging
import os
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

SEATS_HEADER = [
    "seat_id", "event_id", "section", "row", "seat_number",
    "price", "status", "booked_by_user_id",
]
LOG_HEADER = ["timestamp", "user_id", "event_id", "seat_id", "action", "result", "message"]

HDFS_COMMAND_TIMEOUT = 60


class ExportTarget:
    """Interface both targets implement."""

    def append_log_rows(self, rows: list[list]) -> None:
        raise NotImplementedError

    def replace_seats_snapshot(self, rows: list[list]) -> None:
        raise NotImplementedError


class LocalDirectoryTarget(ExportTarget):
    """Mirrors to a folder on this machine.

    The snapshot is written to a temporary file and then moved into place, so
    a reader that opens seats.csv mid-export sees the previous complete file
    rather than a half-written one.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.log_path = self.root / "bookings" / "booking_log.csv"
        self.seats_path = self.root / "seats" / "csv" / "seats.csv"
        self._lock = threading.Lock()

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.seats_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self._write_rows(self.log_path, [LOG_HEADER])

    def append_log_rows(self, rows: list[list]) -> None:
        if not rows:
            return
        with self._lock, self.log_path.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)

    def replace_seats_snapshot(self, rows: list[list]) -> None:
        temporary = self.seats_path.with_suffix(".csv.tmp")
        with self._lock:
            self._write_rows(temporary, [SEATS_HEADER, *rows])
            os.replace(temporary, self.seats_path)

    @staticmethod
    def _write_rows(path: Path, rows: list[list]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)


class HdfsTarget(ExportTarget):
    """Mirrors to HDFS through the `hdfs dfs` client.

    Shelling out rather than using a Python HDFS library keeps the dependency
    list at one package and matches how Part 1 loaded the cluster. Every call
    is bounded by a timeout: an unreachable NameNode otherwise leaves the
    export thread blocked indefinitely.
    """

    def __init__(self, hdfs_root: str):
        self.hdfs_root = hdfs_root.rstrip("/")
        self.log_path = f"{self.hdfs_root}/bookings/booking_log.csv"
        self.seats_path = f"{self.hdfs_root}/seats/csv/seats.csv"
        self._lock = threading.Lock()

        self._run(["hdfs", "dfs", "-mkdir", "-p", f"{self.hdfs_root}/bookings"])
        self._run(["hdfs", "dfs", "-mkdir", "-p", f"{self.hdfs_root}/seats/csv"])
        if not self._exists(self.log_path):
            self._put(self.log_path, _to_csv([LOG_HEADER]))

    def append_log_rows(self, rows: list[list]) -> None:
        if not rows:
            return
        with self._lock:
            self._run(["hdfs", "dfs", "-appendToFile", "-", self.log_path],
                      stdin=_to_csv(rows))

    def replace_seats_snapshot(self, rows: list[list]) -> None:
        with self._lock:
            self._put(self.seats_path, _to_csv([SEATS_HEADER, *rows]))

    def _put(self, path: str, text: str) -> None:
        self._run(["hdfs", "dfs", "-put", "-f", "-", path], stdin=text)

    def _exists(self, path: str) -> bool:
        return self._run(["hdfs", "dfs", "-test", "-e", path], check=False) == 0

    @staticmethod
    def _run(command: list[str], *, stdin: str | None = None, check: bool = True) -> int:
        try:
            completed = subprocess.run(
                command,
                input=stdin.encode("utf-8") if stdin else None,
                capture_output=True,
                timeout=HDFS_COMMAND_TIMEOUT,
            )
        except FileNotFoundError as exc:
            raise ExportUnavailable(
                "The hdfs client is not on PATH. Point --storage-root at a local "
                "folder, or install and configure the Hadoop client."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ExportUnavailable(f"hdfs command timed out after {HDFS_COMMAND_TIMEOUT}s.") from exc

        if check and completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
            raise ExportUnavailable(
                f"hdfs {' '.join(command[2:4])} failed: {detail[-1] if detail else 'unknown error'}"
            )
        return completed.returncode


class ExportUnavailable(RuntimeError):
    """The mirror could not be written. Never fatal to a booking."""


def _to_csv(rows: list[list]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue()


def make_target(storage_root: str) -> ExportTarget:
    """Choose a target from the root: an hdfs:// URI, or a local path."""
    if storage_root.startswith("hdfs://"):
        logger.info("Mirroring bookings to HDFS at %s", storage_root)
        return HdfsTarget(storage_root)
    logger.info("Mirroring bookings to local folder %s", storage_root)
    return LocalDirectoryTarget(storage_root)
