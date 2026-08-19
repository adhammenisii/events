"""Read-only endpoints for the catalogue and seating charts."""

from flask import Blueprint, jsonify

from ..session_cookie import current_user, login_required
from .api_helpers import services

blueprint = Blueprint("events", __name__, url_prefix="/api/events")


@blueprint.get("")
@login_required
def list_events():
    return jsonify({"events": services().catalog.list_events()})


@blueprint.get("/<event_id>")
@login_required
def get_event(event_id: str):
    catalog = services().catalog
    event = catalog.get_event(event_id)
    return jsonify({"event": event.to_dict(), "stats": catalog.event_stats(event_id).to_dict()})


@blueprint.get("/<event_id>/seats")
@login_required
def get_seat_map(event_id: str):
    """Everything one render needs: the event, its seats and its statistics.

    ``mine`` is resolved here rather than in the browser so the client never
    has to know any user id but its own.
    """
    event, seats, stats = services().catalog.seat_map(event_id)
    viewer_id = current_user().user_id
    return jsonify(
        {
            "event": event.to_dict(),
            "stats": stats.to_dict(),
            "seats": [
                {**seat.to_dict(), "mine": seat.booked_by_user_id == viewer_id} for seat in seats
            ],
        }
    )


@blueprint.get("/<event_id>/stats")
@login_required
def get_event_stats(event_id: str):
    return jsonify({"stats": services().catalog.event_stats(event_id).to_dict()})
