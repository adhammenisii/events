"""Sign in, sign out, and account creation.

The session lives at a single URL treated as a resource: POST creates one,
GET reads the current one, DELETE ends it.
"""

from flask import Blueprint, jsonify, make_response

from ...config import SESSION_TTL_SECONDS
from ...errors import AuthenticationError
from ..session_cookie import attach_session, clear_session, current_user, read_token
from .api_helpers import json_body, required_field, services

blueprint = Blueprint("auth", __name__, url_prefix="/api")


@blueprint.get("/session")
def read_session():
    user = current_user()
    if user is None:
        raise AuthenticationError("No active session.")
    return jsonify({"user": user.to_dict()})


@blueprint.post("/session")
def create_session():
    body = json_body()
    session = services().auth.log_in(
        required_field(body, "email"), required_field(body, "password")
    )
    response = make_response(jsonify({"user": session.user.to_dict()}))
    return attach_session(response, session.token, SESSION_TTL_SECONDS)


@blueprint.delete("/session")
def end_session():
    services().auth.log_out(read_token())
    return clear_session(make_response(jsonify({"signed_out": True})))


@blueprint.post("/accounts")
def create_account():
    body = json_body()
    session = services().auth.register(
        required_field(body, "full_name"),
        required_field(body, "email"),
        required_field(body, "password"),
    )
    response = make_response(jsonify({"user": session.user.to_dict()}), 201)
    return attach_session(response, session.token, SESSION_TTL_SECONDS)


@blueprint.get("/demo-accounts")
def list_demo_accounts():
    """Seeded accounts offered on the login page as one-click fills.

    Only exposed because this runs on sample data; a real deployment would
    not advertise which addresses have credentials.
    """
    accounts = services().auth.demo_accounts(limit=6)
    return jsonify(
        {
            "accounts": [{"full_name": u.full_name, "email": u.email} for u in accounts],
            "password": services().demo_password,
        }
    )
