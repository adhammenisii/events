"""Turns exceptions into JSON, in one place.

Routes never build an error response by hand. They raise (or let a service
raise) and the handlers below decide the status code and body, so every
endpoint fails in the same shape:

    {"error": {"code": "seat_already_booked", "message": "..."}}
"""

import logging

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from ..errors import BoxOfficeError

logger = logging.getLogger(__name__)


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(BoxOfficeError)
    def handle_domain_error(error: BoxOfficeError):
        # Expected outcomes -- a taken seat, a bad password. Logged at debug
        # level because they are the system working, not failing.
        logger.debug("%s on %s: %s", error.code, request.path, error.message)
        return jsonify({"error": error.to_dict()}), error.status

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        if not request.path.startswith("/api/"):
            return error  # let Flask render its own page for non-API routes
        return (
            jsonify({"error": {"code": _slug(error.name), "message": error.description}}),
            error.code,
        )

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        # Anything reaching here is a bug. The traceback goes to the log; the
        # client gets a generic message, because exception text has a habit of
        # containing file paths and SQL.
        logger.exception("Unhandled error on %s %s", request.method, request.path)
        return (
            jsonify(
                {
                    "error": {
                        "code": "internal_error",
                        "message": "Something went wrong on our side. Please try again.",
                    }
                }
            ),
            500,
        )


def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "_")
