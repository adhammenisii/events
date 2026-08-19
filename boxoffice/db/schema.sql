-- Box Office schema.
--
-- Seat state lives here rather than in process memory: it has to survive a
-- restart, and the availability check has to stay correct when more than one
-- worker is serving requests. Booking is a single conditional UPDATE guarded
-- by the CHECK below, so the database itself is what rejects a double
-- booking -- no application-level lock is load bearing.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    name        TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT '',
    venue       TEXT    NOT NULL DEFAULT '',
    city        TEXT    NOT NULL DEFAULT '',
    event_date  TEXT    NOT NULL DEFAULT '',
    event_time  TEXT    NOT NULL DEFAULT '',
    total_seats INTEGER NOT NULL DEFAULT 0,
    base_price  REAL    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    full_name     TEXT NOT NULL,
    email         TEXT NOT NULL,
    phone         TEXT NOT NULL DEFAULT '',
    signup_date   TEXT NOT NULL DEFAULT '',
    password_hash TEXT,          -- NULL for seeded records with no login yet
    password_salt TEXT
);

-- Emails are matched case-insensitively at login, so the uniqueness
-- constraint has to be case-insensitive too.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users (email COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS seats (
    seat_id           TEXT PRIMARY KEY,
    event_id          TEXT    NOT NULL REFERENCES events (event_id) ON DELETE CASCADE,
    section           TEXT    NOT NULL,
    row_label         TEXT    NOT NULL,
    seat_number       INTEGER NOT NULL,
    price             REAL    NOT NULL CHECK (price >= 0),
    status            TEXT    NOT NULL CHECK (status IN ('available', 'booked')),
    booked_by_user_id TEXT    REFERENCES users (user_id) ON DELETE SET NULL,
    booked_at         TEXT,
    -- A booked seat always names its owner, an available one never does.
    -- Without this the two columns could drift apart on a partial write.
    CHECK (
        (status = 'booked'    AND booked_by_user_id IS NOT NULL) OR
        (status = 'available' AND booked_by_user_id IS NULL)
    )
);

-- Covers the seat-map read (ordered listing for one event) end to end.
CREATE INDEX IF NOT EXISTS idx_seats_event_layout
    ON seats (event_id, section, row_label, seat_number);

-- Covers the per-event availability counters and the revenue sum.
CREATE INDEX IF NOT EXISTS idx_seats_event_status ON seats (event_id, status);

-- "My bookings" lookups.
CREATE INDEX IF NOT EXISTS idx_seats_owner ON seats (booked_by_user_id)
    WHERE booked_by_user_id IS NOT NULL;

-- Append-only record of every attempt, successful or not. Rejections are the
-- interesting half: this is where a lost race is visible after the fact.
CREATE TABLE IF NOT EXISTS booking_log (
    entry_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    user_id    TEXT NOT NULL DEFAULT '',
    event_id   TEXT NOT NULL DEFAULT '',
    seat_id    TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL CHECK (action IN ('book', 'cancel')),
    result     TEXT NOT NULL,
    message    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_booking_log_seat ON booking_log (seat_id, entry_id);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions (expires_at);
