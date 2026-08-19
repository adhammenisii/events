"""Serves the two HTML pages and their assets.

The booking floor sits behind the login gate, so an unauthenticated visitor
is redirected rather than shown an empty seating chart that fails on every
API call it makes.
"""

from pathlib import Path

from flask import Blueprint, jsonify, redirect, send_from_directory, url_for

from ..session_cookie import current_user
from .api_helpers import services

STATIC_DIR = Path(__file__).resolve().parents[3] / "static"

blueprint = Blueprint("pages", __name__)


@blueprint.get("/")
def booking_floor():
    if current_user() is None:
        return redirect(url_for("pages.login_page"))
    return send_from_directory(STATIC_DIR, "index.html")


@blueprint.get("/login")
def login_page():
    if current_user() is not None:
        return redirect(url_for("pages.booking_floor"))
    return send_from_directory(STATIC_DIR, "login.html")


@blueprint.get("/assets/<path:filename>")
def asset(filename: str):
    """Stylesheets, scripts and anything else under static/.

    Namespaced under /assets so that a future API path can never be shadowed
    by a file that happens to share its name.
    """
    return send_from_directory(STATIC_DIR, filename)


@blueprint.get("/healthz")
def health_check():
    """Liveness probe: the process is up and the database answers."""
    services().db.check_ready()
    return jsonify({"status": "ok"})
