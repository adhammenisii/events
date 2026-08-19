# Architecture — Real-Time Booking Service

## The problem this part solves

Two people click the same seat at almost the same instant. Exactly one must
get it; the other must get a clean rejection. Never both, and never a seat
left in a state where nobody can tell who owns it.

## Where seat state lives, and why

Part 1's HDFS is built for large sequential throughput — ideal for storing and
batch-processing millions of rows, wrong for "check one row and update it,
safely, many times a second". Using it as the live source of truth would be
slow, and it would not solve the race on its own: two processes could both
read `available` before either wrote `booked`.

So the booking service keeps seat state in **SQLite**, and treats HDFS as what
Part 1 designed it to be — durable storage the rest of the system reads from,
updated after every change rather than consulted on the hot path.

An earlier version of this service kept seats in memory behind one
`threading.Lock` per seat. That is correct for a single process, and it is
what the first iteration shipped with, but it has two limits worth naming:

- **Nothing survives a restart.** Every booking was gone when the process
  stopped, which is not a property a ticket system can have.
- **The guarantee ends at the process boundary.** Two workers behind a load
  balancer each hold their own locks and their own copy of the seat map, so
  they would happily sell the same seat twice.

Moving the state into a database fixes both, and the concurrency guarantee
gets *stronger* rather than weaker, because it stops depending on there being
exactly one process.

## How double-booking is prevented

Booking is one statement:

```sql
UPDATE seats
   SET status = 'booked', booked_by_user_id = ?, booked_at = ?
 WHERE seat_id = ? AND status = 'available';
```

The service asks the driver how many rows that changed:

- **1** — this caller took the seat.
- **0** — the seat was already gone. Reported as `seat_already_booked`.

There is no gap between the check and the write for a competing booking to slip
into, because the check *is* the write. SQLite serialises writers, so of two
transactions running this for the same seat, one commits its change and the
other then finds nothing matching `status = 'available'`.

Cancellation works the same way, with ownership in the predicate rather than
in an `if` statement above it:

```sql
UPDATE seats
   SET status = 'available', booked_by_user_id = NULL, booked_at = NULL
 WHERE seat_id = ? AND status = 'booked' AND booked_by_user_id = ?;
```

Two supporting choices make this hold up under load:

- **WAL mode**, so readers — every seat map request — never block the writer.
- **`BEGIN IMMEDIATE`**, so a write transaction takes the write lock at the
  start instead of upgrading halfway through. Contention becomes a short wait
  up front rather than a failure after the caller believes it holds the row.

A schema-level `CHECK` backs all of this up: a row must either be `booked`
with an owner or `available` with none. No code path can leave the two columns
disagreeing, whatever it does.

## Booking hits the seat that was asked for

The `seat_id` in the request is the only seat the statement can touch — it is
the primary key in the `WHERE` clause. There is no seat-selection logic
anywhere in the service: no "nearest available", no fallback to the next seat
in the row. A request for a seat that is gone is refused, never substituted.

That matters because the alternative failure is silent. A system that quietly
hands out a different seat when the requested one is taken looks like it is
working, right up until somebody arrives at the venue holding a ticket for a
seat they did not choose.

Availability is checked as part of the same statement rather than before it.
An explicit read-then-write would look more careful and be less safe:

```python
seat = repository.get(seat_id)          # says "available"
if seat.status == "available":          # <- another booking commits here
    repository.mark_booked(seat_id)     # overwrites it
```

`tests/test_seat_targeting.py` holds this down by comparing every seat in the
event before and after each request: a successful booking must change exactly
one row, and a refused one must change none.

## The roster outnumbers the venue

Every seat has to be claimable by a distinct account, so the number of
registered users is kept above the number of seats. The Part 1 extract has 200
users against 2,550 seats, so seeding generates the shortfall
(`top_up_users()` in `db/bootstrap.py`).

The bound is *total* seats, not currently-available ones. Availability moves
with every booking and cancellation, so an invariant written against it would
hold or fail depending on the hour; more users than seats implies more users
than available seats at every moment, and more than any single event can hold.
The top-up is idempotent and runs at startup, so a database created before the
rule existed is repaired rather than left short.

## Verifying it

`tests/test_concurrent_same_seat.py` releases 25 threads through a
`threading.Barrier` so they genuinely overlap, and asserts exactly one
succeeds, the surviving row is consistent, and all 25 attempts — the winner
and the 24 refusals — are in the audit log.

`tests/test_concurrent_different_seats.py` is the counterpart: 25 threads on
25 different seats must all succeed, each seat ending up owned by its own
booker. Correctness alone is easy to get by serialising everything; this is
what shows unrelated bookings are not blocked by each other.

`scripts/race_demo.py` repeats the first test over real HTTP, with a separate
signed-in session per request.

## Layers

```
Browser  static/js/booking-page.js
   |     fetch POST /api/bookings {event_id, seat_id}
   v
Routes   boxoffice/web/routes/bookings.py
   |     checks the session, reads the user id from it, never from the body
   v
Service  boxoffice/services/booking_service.py
   |     validates, decides what a failure means, records the attempt
   v
Repository  boxoffice/repositories/seats.py
   |     the conditional UPDATE
   v
SQLite   instance/boxoffice.db
   |
   +--> background export --> /ticket_system on HDFS or a local folder
```

Each layer only knows about the one below it. Routes contain no SQL, services
contain no Flask, and repositories contain no business rules — which is what
makes the services testable against a temporary database with no server
running, as the test suite does.

## Identity

"Booking as" used to be a dropdown, which meant the browser chose whose name a
booking was made under — anyone could book as anyone. It is now a session:

- Passwords are stored as PBKDF2-HMAC-SHA256 with a per-user random salt.
- Signing in creates a row in `sessions` and returns an opaque token in an
  HttpOnly, SameSite=Lax cookie. The token carries no information.
- The user id used for a booking is resolved from that row, server-side.
- Sign-out deletes the row, so a leaked token stops working immediately.
- Repeated failures for one address are throttled, and an unknown address
  costs the same time as a wrong password so the two cannot be told apart.

## What is written back to Part 1 storage, and when

After each committed change, a background thread writes:

1. **The audit log** — every attempt, successful or refused — appended to
   `/ticket_system/bookings/booking_log.csv`.
2. **A seat snapshot** — `/ticket_system/seats/csv/seats.csv` rewritten with
   current status, keeping Part 2's batch jobs in step with live bookings.

The snapshot is ~2,500 rows, and writing it to HDFS can take seconds, so it
does not run inside the request. While a write is in flight, further bookings
collapse into a single pending pass — the mirror converges on current state
without the queue growing. Failures are logged and retried on the next pass;
they never affect a booking, because the database is the source of truth and
the mirror is a convenience for what reads it downstream.

## What would change at larger scale

SQLite comfortably handles a venue, or a few hundred. The layering means
outgrowing it is a repository change rather than a rewrite: the conditional
`UPDATE` is standard SQL and behaves identically on PostgreSQL, where writers
would then be concurrent rather than serialised. The service, the routes and
the frontend would not need to change.
