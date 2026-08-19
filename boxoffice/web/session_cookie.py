"""Reading and writing the session cookie, plus the login guard for routes."""

import functools

from flask import current_app, g, request

from ..errors import AuthenticationError
from ..models import User

COOKIE_NAME = "boxoffice_session"


def read_token() -> str | None:
    return request.cookies.get(COOKIE_NAME)


def attach_session(response, token: str, max_age_seconds: int):
    """Set the session cookie on a response.

    HttpOnly keeps the token out of reach of any script on the page, and
    SameSite=Lax means another site cannot make an authenticated booking on
    the visitor behalf. Secure follows the scheme so the cookie still works
    over plain HTTP in local development.
    """
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max_age_seconds,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
        path="/",
    )
    return response


def clear_session(response):
    response.delete_cookie(COOKIE_NAME, path="/", samesite="Lax")
    return response


def current_user() -> User | None:
    """The signed-in user, resolved once per request and cached on ``g``."""
    if "current_user" not in g:
        g.current_user = current_app.extensions["boxoffice"].auth.resolve_session(read_token())
    return g.current_user


def login_required(view):
    """Refuse the request unless a valid session cookie is present."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            raise AuthenticationError("Sign in to continue.")
        return view(*args, **kwargs)

    return wrapper
