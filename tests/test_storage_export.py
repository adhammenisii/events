"""Seeding behaviour and the mirror written for the Part 1 storage layout."""

import csv
import shutil
import tempfile
from pathlib import Path

from support import SEED_DATA_DIR, TemporaryDatabase, available_seats, run_tests, user_ids

from boxoffice.db.bootstrap import SeedDataError, seed_from_csv
from boxoffice.export import ExportUnavailable, StorageExporter, make_target
from boxoffice.export.targets import HdfsTarget, LocalDirectoryTarget
from boxoffice.repositories import SeatRepository
from boxoffice.services import BookingService

EVENT_ID = "EVT00011"


def test_reseeding_does_not_cancel_live_bookings():
    """Re-running the seed refreshes layout data without wiping real bookings."""
    with TemporaryDatabase() as db:
        seat = available_seats(db, EVENT_ID)[0]
        user = user_ids(db, 1)[0]
        BookingService(db).book_seat(user_id=user, event_id=EVENT_ID, seat_id=seat.seat_id)

        seed_from_csv(db, SEED_DATA_DIR, demo_accounts=0)

        with db.read() as connection:
            after = SeatRepository(connection).get(seat.seat_id)
        assert after.status == "booked", "a re-seed must not release booked seats"
        assert after.booked_by_user_id == user


def test_missing_seed_file_reports_which_one():
    with TemporaryDatabase() as db:
        empty = Path(tempfile.mkdtemp(prefix="boxoffice-empty-"))
        try:
            seed_from_csv(db, empty, demo_accounts=0)
        except SeedDataError as error:
            assert "events.csv" in error.message
        else:
            raise AssertionError("expected SeedDataError for an empty seed folder")


def test_export_writes_the_audit_log_and_the_seat_snapshot():
    with TemporaryDatabase() as db:
        root = Path(tempfile.mkdtemp(prefix="boxoffice-export-"))
        exporter = StorageExporter(db, make_target(str(root)))
        exporter.start()
        try:
            booking = BookingService(db, on_change=exporter.request_export)
            seat = available_seats(db, EVENT_ID)[0]
            user = user_ids(db, 1)[0]
            booking.book_seat(user_id=user, event_id=EVENT_ID, seat_id=seat.seat_id)
            exporter.export_now()
        finally:
            exporter.stop()

        log_rows = _read_csv(root / "bookings" / "booking_log.csv")
        assert any(
            row["seat_id"] == seat.seat_id and row["result"] == "booking_successful"
            for row in log_rows
        ), "the booking should appear in the exported audit log"

        snapshot = {row["seat_id"]: row for row in _read_csv(root / "seats" / "csv" / "seats.csv")}
        assert snapshot[seat.seat_id]["status"] == "booked"
        assert snapshot[seat.seat_id]["booked_by_user_id"] == user
        assert len(snapshot) == 2550, "the snapshot should cover every seat"


def test_export_does_not_repeat_log_rows_it_has_already_written():
    with TemporaryDatabase() as db:
        root = Path(tempfile.mkdtemp(prefix="boxoffice-export-"))
        exporter = StorageExporter(db, make_target(str(root)))
        exporter.start()
        try:
            booking = BookingService(db)
            seats = available_seats(db, EVENT_ID, 2)
            user = user_ids(db, 1)[0]
            for seat in seats:
                booking.book_seat(user_id=user, event_id=EVENT_ID, seat_id=seat.seat_id)

            exporter.export_now()
            after_first = len(_read_csv(root / "bookings" / "booking_log.csv"))
            exporter.export_now()
            after_second = len(_read_csv(root / "bookings" / "booking_log.csv"))
        finally:
            exporter.stop()

        assert after_first == 2, f"expected two exported rows, found {after_first}"
        assert after_second == after_first, "a second pass must not duplicate rows"


def test_storage_root_selects_the_matching_target():
    root = Path(tempfile.mkdtemp(prefix="boxoffice-target-"))
    local = make_target(str(root))
    assert isinstance(local, LocalDirectoryTarget)
    assert local.log_path.exists(), "the local target creates its log file up front"


def test_hdfs_target_explains_itself_when_the_client_is_missing():
    """Without the hdfs CLI the failure should name the cause, not raise FileNotFoundError."""
    if shutil.which("hdfs"):
        return  # a real client is installed; the failure path cannot be provoked here

    try:
        HdfsTarget("hdfs://localhost:9000/ticket_system")
    except ExportUnavailable as error:
        assert "hdfs client" in str(error)
    else:
        raise AssertionError("expected ExportUnavailable when the hdfs client is absent")


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


if __name__ == "__main__":
    raise SystemExit(run_tests(dict(globals())))
