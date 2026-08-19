"""Time helpers.

Timestamps are stored as UTC ISO-8601 strings. SQLite has no native date
type, and ISO-8601 in UTC is the one text format that still sorts and
compares correctly as a string -- which is what the session-expiry query and
the audit log ordering rely on.
"""

from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="seconds")


def utc_iso_after(seconds: int) -> str:
    return (utc_now() + timedelta(seconds=seconds)).isoformat(timespec="seconds")
