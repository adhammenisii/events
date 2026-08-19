"""Fire simultaneous booking requests at one seat over real HTTP.

The test suite proves the guarantee against the service layer. This proves it
end to end -- through the network stack, the session layer and the Flask
threading model -- which is the version worth showing to somebody in person.

    python app.py --port 5000                       # in one terminal
    python scripts/race_demo.py --requests 20       # in another

Every request is a separate account with its own session, all released at the
same instant. Exactly one should come back 201.
"""

import argparse
import json
import sys
import threading
import urllib.error
import urllib.request
from collections import Counter
from http.cookiejar import CookieJar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boxoffice.db import Database                    # noqa: E402
from boxoffice.errors import DuplicateAccountError    # noqa: E402
from boxoffice.services import AuthService           # noqa: E402

DEMO_PASSWORD = "race-demo-password"


class Client:
    """One HTTP session, with its own cookie jar."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")


def prepare_accounts(database: Path, count: int) -> list[str]:
    """Create accounts with known passwords so each client can sign in.

    Written straight to the database rather than through the registration
    endpoint: the point of the demo is the booking race, and doing the setup
    over HTTP would add a minute of password hashing to the front of it.
    """
    auth = AuthService(Database(database))
    emails = []
    for index in range(count):
        email = f"race-demo-{index}@example.com"
        try:
            auth.register(f"Race Demo {index}", email, DEMO_PASSWORD)
        except DuplicateAccountError:
            pass  # left over from an earlier run of this script
        emails.append(email)
    return emails


def pick_contested_seat(base_url: str, email: str) -> tuple[str, str]:
    scout = Client(base_url)
    status, _ = scout.call("POST", "/api/session", {"email": email, "password": DEMO_PASSWORD})
    if status != 200:
        raise SystemExit(f"Could not sign in as {email} (HTTP {status}). Is the server running?")

    _, payload = scout.call("GET", "/api/events")
    for event in payload["events"]:
        _, seat_map = scout.call("GET", f"/api/events/{event['event_id']}/seats")
        for seat in seat_map["seats"]:
            if seat["status"] == "available":
                return event["event_id"], seat["seat_id"]
    raise SystemExit("Every seat in every event is already booked.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--requests", type=int, default=15)
    parser.add_argument("--database", type=Path, default=Path("instance/boxoffice.db"))
    arguments = parser.parse_args()

    print(f"Preparing {arguments.requests} accounts...")
    emails = prepare_accounts(arguments.database, arguments.requests)

    event_id, seat_id = pick_contested_seat(arguments.base_url, emails[0])
    print(f"Contested seat: {seat_id} ({event_id})\n")

    clients = [Client(arguments.base_url) for _ in emails]
    for client, email in zip(clients, emails):
        client.call("POST", "/api/session", {"email": email, "password": DEMO_PASSWORD})

    results: list[tuple[int, str] | None] = [None] * len(clients)
    barrier = threading.Barrier(len(clients))

    def attempt(index: int, client: Client) -> None:
        barrier.wait()  # release every thread at the same instant
        status, payload = client.call(
            "POST", "/api/bookings", {"event_id": event_id, "seat_id": seat_id}
        )
        label = payload.get("status") or payload.get("error", {}).get("code", "unknown")
        results[index] = (status, label)

    threads = [threading.Thread(target=attempt, args=(i, c)) for i, c in enumerate(clients)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    tally = Counter(f"{status} {label}" for status, label in results)
    for outcome, count in sorted(tally.items()):
        print(f"  {count:3d} x  {outcome}")

    winners = sum(1 for status, _ in results if status == 201)
    print(f"\n{winners} of {len(results)} requests booked the seat.")
    if winners != 1:
        print("FAILED: exactly one request should have succeeded.")
        return 1
    print("PASSED: the seat was sold exactly once.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
