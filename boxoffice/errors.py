"""Domain-level errors.

Services raise these; the web layer is the only place that knows how to
turn them into HTTP responses (see ``boxoffice.web.errors``). Keeping the
mapping in one place means a service never has to import Flask, and a new
route gets consistent error output for free.
"""


class BoxOfficeError(Exception):
    """Base class for every error this application raises deliberately.

    ``code`` is a stable machine-readable string the frontend switches on;
    ``status`` is the HTTP status the web layer should reply with. Anything
    that escapes without being a BoxOfficeError is treated as a bug and
    reported as a 500.
    """

    code = "internal_error"
    status = 500

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        payload = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(BoxOfficeError):
    """The request was malformed — missing fields, wrong types, bad values."""

    code = "invalid_request"
    status = 400


class AuthenticationError(BoxOfficeError):
    """No valid session, or the supplied credentials did not match."""

    code = "not_authenticated"
    status = 401


class NotFoundError(BoxOfficeError):
    """The referenced event, seat or user does not exist."""

    code = "not_found"
    status = 404


class SeatNotFoundError(NotFoundError):
    code = "seat_unavailable"


class ConflictError(BoxOfficeError):
    """The request was valid but lost a race — the seat is already taken."""

    code = "conflict"
    status = 409


class SeatUnavailableError(ConflictError):
    code = "seat_already_booked"


class CancelRejectedError(ConflictError):
    code = "cancel_failed"


class DuplicateAccountError(ConflictError):
    code = "email_already_registered"


class StorageError(BoxOfficeError):
    """The database could not be reached or refused the write."""

    code = "storage_unavailable"
    status = 503
