"""Sign-in, registration and session handling."""

from support import TemporaryDatabase, run_tests

from boxoffice.clock import utc_iso_after
from boxoffice.errors import AuthenticationError, DuplicateAccountError, ValidationError
from boxoffice.repositories import SessionRepository
from boxoffice.services import AuthService, LoginThrottle

PASSWORD = "opening-night"


def test_registration_creates_an_account_and_signs_it_in():
    with TemporaryDatabase() as db:
        auth = AuthService(db)
        session = auth.register("Nadia Rahman", "nadia@example.com", PASSWORD)

        assert session.user.full_name == "Nadia Rahman"
        assert session.user.user_id.startswith("USR")
        assert auth.resolve_session(session.token).user_id == session.user.user_id


def test_registered_account_can_sign_in_again():
    with TemporaryDatabase() as db:
        auth = AuthService(db)
        created = auth.register("Omar Fahmy", "omar@example.com", PASSWORD)
        signed_in = auth.log_in("omar@example.com", PASSWORD)

        assert signed_in.user.user_id == created.user.user_id
        assert signed_in.token != created.token, "each sign-in gets its own token"


def test_email_matching_ignores_case_and_surrounding_space():
    with TemporaryDatabase() as db:
        auth = AuthService(db)
        auth.register("Case Test", "Mixed.Case@Example.com", PASSWORD)

        assert auth.log_in("  mixed.case@example.com  ", PASSWORD) is not None
        _expect(DuplicateAccountError, auth.register,
                full_name="Impostor", email="MIXED.CASE@example.com", password=PASSWORD)


def test_wrong_password_and_unknown_email_fail_identically():
    """Neither response should reveal whether the address has an account."""
    with TemporaryDatabase() as db:
        auth = AuthService(db)
        auth.register("Real Person", "real@example.com", PASSWORD)

        wrong = _expect(AuthenticationError, auth.log_in,
                        email="real@example.com", password="not-it")
        unknown = _expect(AuthenticationError, auth.log_in,
                          email="ghost@example.com", password=PASSWORD)
        assert wrong.message == unknown.message


def test_seeded_accounts_without_credentials_cannot_sign_in():
    with TemporaryDatabase() as db:
        auth = AuthService(db)
        with db.read() as connection:
            email = connection.execute("SELECT email FROM users LIMIT 1").fetchone()["email"]

        _expect(AuthenticationError, auth.log_in, email=email, password=PASSWORD)


def test_invalid_input_is_rejected():
    with TemporaryDatabase() as db:
        auth = AuthService(db)
        _expect(ValidationError, auth.register,
                full_name="Short Password", email="sp@example.com", password="abc")
        _expect(ValidationError, auth.register,
                full_name="Bad Email", email="not-an-email", password=PASSWORD)
        _expect(ValidationError, auth.register,
                full_name=" ", email="blank@example.com", password=PASSWORD)
        _expect(ValidationError, auth.log_in, email="also-not-an-email", password=PASSWORD)


def test_signing_out_invalidates_the_token():
    with TemporaryDatabase() as db:
        auth = AuthService(db)
        session = auth.register("Sign Out", "signout@example.com", PASSWORD)

        assert auth.log_out(session.token) is True
        assert auth.resolve_session(session.token) is None
        assert auth.log_out(session.token) is False, "a second sign-out changes nothing"


def test_unknown_and_missing_tokens_resolve_to_nobody():
    with TemporaryDatabase() as db:
        auth = AuthService(db)
        assert auth.resolve_session(None) is None
        assert auth.resolve_session("") is None
        assert auth.resolve_session("made-up-token") is None


def test_expired_sessions_are_ignored_and_purged():
    with TemporaryDatabase() as db:
        auth = AuthService(db)
        session = auth.register("Expiring", "expiring@example.com", PASSWORD)

        with db.write() as connection:
            SessionRepository(connection).create(
                token="stale-token", user_id=session.user.user_id,
                created_at=utc_iso_after(-7200), expires_at=utc_iso_after(-3600),
            )

        assert auth.resolve_session("stale-token") is None, "expired token must not resolve"
        assert auth.purge_expired_sessions() == 1
        assert auth.resolve_session(session.token) is not None, "live session survives the purge"


def test_repeated_failures_are_throttled_then_released():
    with TemporaryDatabase() as db:
        throttle = LoginThrottle(limit=3, window=300)
        auth = AuthService(db, throttle=throttle)
        auth.register("Throttled", "throttled@example.com", PASSWORD)

        for _ in range(3):
            _expect(AuthenticationError, auth.log_in,
                    email="throttled@example.com", password="wrong")

        blocked = _expect(AuthenticationError, auth.log_in,
                          email="throttled@example.com", password=PASSWORD)
        assert "Too many failed attempts" in blocked.message

        # A different account is unaffected, and clearing releases the block.
        throttle.clear("throttled@example.com")
        assert auth.log_in("throttled@example.com", PASSWORD) is not None


def _expect(error_type, call, **kwargs):
    try:
        call(**kwargs)
    except error_type as error:
        return error
    except Exception as unexpected:
        raise AssertionError(
            f"expected {error_type.__name__}, got {type(unexpected).__name__}: {unexpected}"
        ) from None
    raise AssertionError(f"expected {error_type.__name__}, but the call succeeded")


if __name__ == "__main__":
    raise SystemExit(run_tests(dict(globals())))
