"""Booking and cancellation endpoints.

The identity used for a booking always comes from the session, never from the
request body. That is the whole point of the login system: before it, a
client could book a seat in any user name it liked.
"""

from flask import Blueprint, jsonify

from ..session_cookie import current_user, login_required
from .api_helpers import json_body, required_field, services

blueprint = Blueprint("bookings", __name__, url_prefix="/api/bookings")


@blueprint.post("")
@login_required
def create_booking():
    body = json_body()
    outcome = services().booking.book_seat(
        user_id=current_user().user_id,
        event_id=required_field(body, "event_id"),
        seat_id=required_field(body, "seat_id"),
    )
    return jsonify(outcome.to_dict()), 201


@blueprint.delete("/<seat_id>")
@login_required
def cancel_booking(seat_id: str):
    outcome = services().booking.cancel_booking(
        user_id=current_user().user_id, seat_id=seat_id
    )
    return jsonify(outcome.to_dict())


@blueprint.get("/mine")
@login_required
def list_my_bookings():
    user = current_user()
    seats = services().catalog.seats_held_by(user.user_id)
    return jsonify({"seats": [seat.to_dict() for seat in seats]})


@blueprint.get("/history")
@login_required
def booking_history():
    """Recent attempts by this user, rejections included."""
    entries = services().booking.history_for_user(current_user().user_id)
    return jsonify({"entries": entries})
