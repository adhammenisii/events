"""HTTP-level tests: authentication gates, status codes and payload shapes."""

from support import TemporaryDatabase, available_seats, run_tests

from boxoffice.config import AppConfig
from boxoffice.services import AuthService
from boxoffice.web import create_app

EVENT_ID = "EVT00010"
PASSWORD = "front-row-seat"
EMAIL = "tester@example.com"


class ApiHarness:
    """An application wired to a throwaway database, plus a signed-in client."""

    def __init__(self):
        self.temporary = TemporaryDatabase()
        self.db = self.temporary.db
        config = AppConfig.from_environment().merged_with(
            database_path=self.db.path, export_enabled=False
        )
        self.app = create_app(config)
        self.client = self.app.test_client()

    def sign_in(self):
        AuthService(self.db).register("Api Tester", EMAIL, PASSWORD)
        response = self.client.post("/api/session", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 200, response.get_json()
        return response.get_json()["user"]

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.temporary.close()


def test_every_data_endpoint_requires_a_session():
    with ApiHarness() as api:
        protected = [
            ("get", "/api/events", None),
            ("get", f"/api/events/{EVENT_ID}/seats", None),
            ("get", f"/api/events/{EVENT_ID}/stats", None),
            ("get", "/api/bookings/mine", None),
            ("post", "/api/bookings", {"event_id": EVENT_ID, "seat_id": "x"}),
            ("delete", "/api/bookings/x", None),
        ]
        for method, path, body in protected:
            response = getattr(api.client, method)(path, json=body)
            assert response.status_code == 401, f"{method.upper()} {path} was not protected"
            assert response.get_json()["error"]["code"] == "not_authenticated"


def test_unauthenticated_visitor_is_sent_to_the_login_page():
    with ApiHarness() as api:
        response = api.client.get("/")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")


def test_signed_in_visitor_is_sent_away_from_the_login_page():
    with ApiHarness() as api:
        api.sign_in()
        response = api.client.get("/login")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")


def test_session_cookie_is_not_readable_by_scripts():
    with ApiHarness() as api:
        AuthService(api.db).register("Cookie Check", "cookie@example.com", PASSWORD)
        response = api.client.post(
            "/api/session", json={"email": "cookie@example.com", "password": PASSWORD}
        )
        cookie = response.headers["Set-Cookie"]
        assert "HttpOnly" in cookie
        assert "SameSite=Lax" in cookie


def test_seat_map_marks_the_viewers_own_bookings():
    with ApiHarness() as api:
        user = api.sign_in()
        seat = available_seats(api.db, EVENT_ID)[0]

        api.client.post("/api/bookings", json={"event_id": EVENT_ID, "seat_id": seat.seat_id})
        payload = api.client.get(f"/api/events/{EVENT_ID}/seats").get_json()

        booked = next(s for s in payload["seats"] if s["seat_id"] == seat.seat_id)
        assert booked["mine"] is True
        assert booked["booked_by_user_id"] == user["user_id"]
        assert all(not s["mine"] for s in payload["seats"] if s["seat_id"] != seat.seat_id)


def test_booking_response_carries_the_updated_seat_and_statistics():
    with ApiHarness() as api:
        api.sign_in()
        seat = available_seats(api.db, EVENT_ID)[0]
        before = api.client.get(f"/api/events/{EVENT_ID}/stats").get_json()["stats"]

        response = api.client.post(
            "/api/bookings", json={"event_id": EVENT_ID, "seat_id": seat.seat_id}
        )
        assert response.status_code == 201
        payload = response.get_json()

        assert payload["seat"]["status"] == "booked"
        assert payload["stats"]["booked"] == before["booked"] + 1
        assert payload["stats"]["available"] == before["available"] - 1
        assert payload["stats"]["revenue"] > before["revenue"]


def test_double_booking_over_http_returns_a_conflict():
    with ApiHarness() as api:
        api.sign_in()
        seat = available_seats(api.db, EVENT_ID)[0]
        body = {"event_id": EVENT_ID, "seat_id": seat.seat_id}

        assert api.client.post("/api/bookings", json=body).status_code == 201
        conflict = api.client.post("/api/bookings", json=body)
        assert conflict.status_code == 409
        assert conflict.get_json()["error"]["code"] == "seat_already_booked"


def test_malformed_requests_are_rejected_with_a_reason():
    with ApiHarness() as api:
        api.sign_in()

        no_json = api.client.post("/api/bookings", data="event_id=1")
        assert no_json.status_code == 400
        assert no_json.get_json()["error"]["code"] == "invalid_request"

        missing_field = api.client.post("/api/bookings", json={"event_id": EVENT_ID})
        assert missing_field.status_code == 400
        assert missing_field.get_json()["error"]["details"]["field"] == "seat_id"


def test_unknown_event_returns_a_json_not_found():
    with ApiHarness() as api:
        api.sign_in()
        response = api.client.get("/api/events/EVT99999/seats")
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "not_found"


def test_events_endpoint_reports_separate_statistics_per_event():
    """Task check: figures come from each event's own rows, not a shared constant."""
    with ApiHarness() as api:
        api.sign_in()
        events = api.client.get("/api/events").get_json()["events"]

        assert len(events) > 1
        for event in events:
            stats = event["stats"]
            assert stats["available"] + stats["booked"] == stats["total"]
            assert stats["total"] > 0
        totals = {event["stats"]["total"] for event in events}
        assert len(totals) > 1, "different events should have different seat counts"


def test_signing_out_clears_the_cookie_and_the_session():
    with ApiHarness() as api:
        api.sign_in()
        assert api.client.get("/api/session").status_code == 200

        response = api.client.delete("/api/session")
        assert response.status_code == 200
        assert api.client.get("/api/session").status_code == 401


def test_health_endpoint_is_public():
    with ApiHarness() as api:
        response = api.client.get("/healthz")
        assert response.status_code == 200
        assert response.get_json()["status"] == "ok"


def test_booking_over_http_targets_the_exact_seat_requested():
    """The seat id in the request body is the seat that changes -- no other."""
    with ApiHarness() as api:
        api.sign_in()
        candidates = available_seats(api.db, EVENT_ID, 12)
        target = candidates[7]

        before = _seat_status_map(api)
        response = api.client.post(
            "/api/bookings", json={"event_id": EVENT_ID, "seat_id": target.seat_id}
        )
        assert response.status_code == 201
        payload = response.get_json()
        assert payload["seat"]["seat_id"] == target.seat_id

        after = _seat_status_map(api)
        changed = [seat_id for seat_id in before if before[seat_id] != after[seat_id]]
        assert changed == [target.seat_id], f"these seats moved instead: {changed}"


def test_booking_a_taken_seat_over_http_returns_a_clear_message():
    """Second attempt on the same seat: 409, an explanatory message, no change."""
    with ApiHarness() as api:
        first = api.sign_in()
        seat = available_seats(api.db, EVENT_ID)[0]
        body = {"event_id": EVENT_ID, "seat_id": seat.seat_id}

        assert api.client.post("/api/bookings", json=body).status_code == 201
        before = _seat_status_map(api)

        # A different customer, so this is a genuine clash rather than a repeat.
        rival = api.app.test_client()
        AuthService(api.db).register("Rival Bidder", "rival@example.com", PASSWORD)
        rival.post("/api/session", json={"email": "rival@example.com", "password": PASSWORD})

        refused = rival.post("/api/bookings", json=body)
        assert refused.status_code == 409
        error = refused.get_json()["error"]
        assert error["code"] == "seat_already_booked"
        assert "booked" in error["message"].lower()
        assert error["details"]["seat_id"] == seat.seat_id

        after = _seat_status_map(api)
        assert before == after, "a refused booking must not change any seat"
        assert after[seat.seat_id][1] == first["user_id"], "the first booker keeps the seat"

        # And the rival was not handed something else instead.
        assert rival.get("/api/bookings/mine").get_json()["seats"] == []


def _seat_status_map(api) -> dict:
    payload = api.client.get(f"/api/events/{EVENT_ID}/seats").get_json()
    return {seat["seat_id"]: (seat["status"], seat["booked_by_user_id"])
            for seat in payload["seats"]}


if __name__ == "__main__":
    raise SystemExit(run_tests(dict(globals())))
