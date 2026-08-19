# Box Office — Real-Time Seat Booking

### Parts 3 & 4 of the Distributed Ticket Reservation System

A booking service where signed-in users reserve and release seats, seat state
lives in a real database, and concurrent requests for the same seat can never
double-book it. Comes with the website that drives it.

## Contents

```
dp_part34/
├── app.py                     entry point: parses arguments, starts the server
├── boxoffice/
│   ├── config.py              settings, resolved from defaults, env, then CLI
│   ├── errors.py              domain errors and the HTTP status each maps to
│   ├── models.py              Event, Seat, User, EventStats
│   ├── passwords.py           PBKDF2 hashing and session tokens
│   ├── clock.py               UTC timestamps
│   ├── db/
│   │   ├── schema.sql         tables, constraints, indexes
│   │   ├── connection.py      connection tuning and transaction scopes
│   │   └── bootstrap.py       schema creation and CSV seeding
│   ├── repositories/          SQL, one module per table
│   ├── services/              business logic: booking, catalogue, accounts
│   ├── export/                mirrors changes to the Part 1 storage layout
│   └── web/
│       ├── __init__.py        application factory
│       ├── errors.py          exception to JSON, in one place
│       ├── session_cookie.py  cookie handling and the login guard
│       └── routes/            HTTP endpoints, one blueprint per area
├── static/
│   ├── index.html, login.html
│   ├── css/                   tokens, base, booking floor, sign-in page
│   └── js/                    api client, views, page controllers
├── tests/                     60 tests, runnable without pytest
├── scripts/race_demo.py       fires concurrent bookings over real HTTP
├── docs/                      architecture and how-to-run notes
└── data/                      sample events/seats/users from Part 1
```

## Quickstart

```bash
pip install flask

python tests/run_all.py        # prove the guarantees first
python app.py                  # then run the service and the website
# open http://127.0.0.1:5000
```

The first run creates `instance/boxoffice.db` and seeds it from `data/*.csv`.
Twelve of the sample accounts are given credentials so you can sign straight
in; the login page lists them, and the password is `demo1234`. You can also
create your own account from the same page.

## How it works

**Seat state lives in SQLite.** Booking is a single conditional statement:

```sql
UPDATE seats
   SET status = 'booked', booked_by_user_id = ?, booked_at = ?
 WHERE seat_id = ? AND status = 'available';
```

Two transactions running that for the same seat are serialised by the
database. The first changes one row; the second matches nothing and reports
zero rows changed, which the service turns into a clean `seat_already_booked`.
There is no window between checking and writing, because there is no separate
check. `docs/architecture.md` goes into why this replaced the in-memory lock
that earlier versions used.

**Statistics are derived, never stored.** Availability, occupancy and booked
revenue are computed per event from that event's own seat rows, and every
booking response carries the fresh figures, so the page updates from the same
transaction that made the change.

**Bookings hit the exact seat requested.** The `seat_id` is the primary key in
the `WHERE` clause, and there is no seat-selection logic anywhere in the
service — no "nearest available", no fallback to the next seat along. A seat
that is gone is refused with a message the customer can act on, never
substituted.

**There are always more users than seats.** Every seat has to be claimable by
a distinct account, so seeding tops the roster up past the seat count: 2,600
users against 2,550 seats. See `docs/architecture.md`.

**Identity comes from the session.** The browser holds an opaque token in an
HttpOnly cookie; the user id attached to a booking is looked up server-side
and never accepted from the client.

**Changes are mirrored out to Part 1's storage.** After each commit, a
background thread appends to the audit log and rewrites the seat snapshot
under `/ticket_system` — local folder or HDFS. It coalesces while a write is
in flight, so bookings are never held up by it, and a mirror that is down
cannot fail a booking.

## Requirements, and where each is met

| Requirement | Where |
|---|---|
| Book / cancel a seat | `BookingService.book_seat()` / `.cancel_booking()`, exposed as `POST /api/bookings` and `DELETE /api/bookings/<seat_id>` |
| Prevent double booking under concurrency | Conditional `UPDATE` in `SeatRepository.claim()` — see `docs/architecture.md` |
| Reject already-booked and unknown seats | `seat_already_booked` (409), `seat_unavailable` (404) |
| Seat status readable by the rest of the system | `GET /api/events/<id>/seats`, plus the mirrored `seats.csv` snapshot |
| Bookings stored durably | SQLite `seats` and `booking_log` tables; mirrored to Part 1 storage |
| Test: concurrent requests, same seat, one winner | `tests/test_concurrent_same_seat.py` |
| Test: concurrent requests, different seats, all succeed | `tests/test_concurrent_different_seats.py` |
| Clear result for every request | `booking_successful`, `booking_cancelled`, `seat_already_booked`, `seat_unavailable`, `cancel_failed` |
| Website showing availability and booking | `static/index.html` served at `/` |
| Login for "Booking as" | `static/login.html` at `/login`, `AuthService`, `sessions` table |
| Per-event data, not hardcoded | `events`, `seats` tables; statistics computed per `event_id` |
| Booking targets the exact seat selected | `SeatRepository.claim()` keys on `seat_id`; proved in `tests/test_seat_targeting.py` |
| More registered users than seats | `top_up_users()` during seeding; proved in `tests/test_user_roster.py` |

## Test results

`python tests/run_all.py` — 60 tests across 8 modules, all passing:

- **Same seat, 25 threads released together**: exactly 1 booked, 24 rejected;
  the winning row is consistent and all 25 attempts are in the audit log.
- **25 different seats, 25 threads**: all 25 succeed, each seat owned by its
  own booker, and the event statistics account for every one.
- **Exact-seat targeting**: a successful booking changes exactly one row — the
  one requested — and a refused booking changes none, with no substitute seat
  handed to the rejected booker.
- **Roster invariant**: users outnumber seats overall and per event, and stay
  ahead of availability through rounds of booking and cancellation.
- Booking lifecycle and every refusal path, sign-in and sessions, the HTTP
  layer including its authentication gates, and the storage export.

Verified over real HTTP as well: `scripts/race_demo.py` signs in fifteen
separate sessions and fires their bookings at one seat simultaneously —
1 × `201 booking_successful`, 14 × `409 seat_already_booked`.

The UI was driven end to end in a real browser: sign in, book a seat, watch
the counters move, cancel, switch events, and confirm the row in SQLite.

The HDFS mirror is exercised against a local folder. Pointing it at the real
cluster is one flag — see `docs/how_to_run.md`.
