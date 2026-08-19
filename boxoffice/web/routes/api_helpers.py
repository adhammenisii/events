"""Small helpers shared by the API blueprints."""

from flask import current_app, request

from ...errors import ValidationError


def services():
    """The service container built in :func:`boxoffice.web.create_app`."""
    return current_app.extensions["boxoffice"]


def json_body() -> dict:
    """Parse a JSON request body, or explain why it could not be parsed.

    ``silent=True`` keeps Werkzeug from raising its own 400 with a message
    that says nothing useful, and requiring a JSON content type on
    state-changing requests blocks the simple cross-site form post -- a plain
    HTML form cannot set that header.
    """
    if not request.is_json:
        raise ValidationError("This endpoint expects a JSON request body.")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object.")
    return body


def required_field(body: dict, name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} is required.", details={"field": name})
    return value.strip()
