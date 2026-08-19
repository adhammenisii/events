# How to Run

## 1. Install the dependency

```bash
pip install flask
```

Flask is the only third-party package. Everything else — the database, the
password hashing, the HTTP client in the demo script — is standard library.

## 2. Run the tests

No server needed; each test builds its own temporary database.

```bash
python tests/run_all.py
```

Or one module at a time:

```bash
python tests/test_concurrent_same_seat.py
python tests/test_concurrent_different_seats.py
```

Every module ends with a pass count. `pytest tests` works too, if you have it.

## 3. Start the service

```bash
python app.py
```

Open **http://127.0.0.1:5000**.

The first run creates `instance/boxoffice.db` and seeds it from `data/*.csv`
— 20 events, 2,550 seats and 2,600 users. Later runs reuse the existing
database, so bookings survive a restart.

The roster is deliberately larger than the venue: every seat has to be
claimable by a distinct account. The Part 1 extract carries 200 users against
2,550 seats, so seeding tops it up with generated accounts until there are
more users than seats. Adjust the margin with `--user-headroom`.

**Signing in.** Twelve of the seeded accounts have credentials; the login page
lists them and fills the form when you click one. The password is `demo1234`.
"Create one" registers a fresh account instead.

**Using it.** Pick an event, click any outlined seat to book it (it turns
green), click a green seat to release it. The counters along the top update
from the same response that confirmed the booking. Bookings other people make
appear within about fifteen seconds, or immediately when you return to the
tab.

### Useful flags

```bash
python app.py --port 8000 --host 0.0.0.0   # reachable from another machine
python app.py --debug                      # verbose logs, auto-reload
python app.py --database /tmp/scratch.db   # a separate database
python app.py --no-export                  # skip the storage mirror
```

Each has a `BOXOFFICE_*` environment variable equivalent — `BOXOFFICE_PORT`,
`BOXOFFICE_DATABASE`, `BOXOFFICE_STORAGE_ROOT`, and so on — which is how you
would configure it in a container. Flags win over environment variables.

## 4. Managing the database directly

```bash
# Create and seed explicitly (the app does this on first run anyway)
python -m boxoffice.db.bootstrap

# Start over from the CSVs
python -m boxoffice.db.bootstrap --reset

# More sign-in-able accounts (each costs about a third of a second to hash)
python -m boxoffice.db.bootstrap --reset --demo-accounts 30

# A wider margin of accounts over seats
python -m boxoffice.db.bootstrap --user-headroom 500
```

Topping up the roster is idempotent and runs on every startup, so a database
created before this rule existed is repaired the next time the app starts.

Re-seeding without `--reset` refreshes event and seat details from the CSVs
but leaves live bookings alone, so correcting a price does not cancel anyone's
seat.

To look inside:

```bash
sqlite3 instance/boxoffice.db "SELECT event_id, COUNT(*), SUM(status='booked') FROM seats GROUP BY event_id;"

# Confirm the roster still outnumbers the venue
sqlite3 instance/boxoffice.db   "SELECT (SELECT COUNT(*) FROM users) AS users, (SELECT COUNT(*) FROM seats) AS seats;"
```

## 5. Point it at the Part 1 HDFS cluster

If Part 1's cluster is up and its NameNode port is reachable from this
machine:

```bash
python app.py --storage-root hdfs://localhost:9000/ticket_system
```

Every booking and cancellation is then mirrored into the real
`/ticket_system/bookings/booking_log.csv` and
`/ticket_system/seats/csv/seats.csv`. This needs the `hdfs` CLI on PATH and
configured — the same requirement as Parts 1 and 2. Without it the service
logs a clear warning and carries on: the database is authoritative, and the
mirror is for the batch jobs downstream.

Without the flag, the mirror is written to `storage_output/` locally. Open
either file in an editor while the server runs to watch it update.

## 6. Demonstrating the concurrency guarantee live

With the server running in one terminal:

```bash
python scripts/race_demo.py --requests 20
```

It creates 20 accounts, signs each into its own session, finds a free seat,
and fires all 20 bookings at it simultaneously. Expected output:

```
    1 x  201 booking_successful
   19 x  409 seat_already_booked

1 of 20 requests booked the seat.
PASSED: the seat was sold exactly once.
```

Afterwards, every one of those attempts is in the audit log:

```bash
sqlite3 instance/boxoffice.db \
  "SELECT result, COUNT(*) FROM booking_log GROUP BY result;"
```

## 7. Running it as a real deployment

Flask's built-in server is for development. The application factory takes any
WSGI server:

```bash
pip install waitress
waitress-serve --port 5000 --call boxoffice.web:create_app
```

Configure it through the `BOXOFFICE_*` environment variables, since there is
no argument parser in that path.

## Troubleshooting

**"No module named flask"** — `pip install flask`, adding
`--break-system-packages` on a system-managed Python.

**Port already in use** — `python app.py --port 5001`.

**The page keeps returning to the login screen** — the session cookie is being
dropped. Reach the site through `127.0.0.1` or `localhost` rather than a bare
IP over a proxy that strips cookies.

**A booking says the seat is taken when the map shows it free** — that is the
guarantee working. Someone booked it between the page loading and the click;
the map corrects itself as soon as the rejection comes back.

**Stale data after editing the CSVs** — the database is seeded once. Re-run
`python -m boxoffice.db.bootstrap` to refresh details, or add `--reset` to
rebuild from scratch.
