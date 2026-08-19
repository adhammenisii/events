"""Queries against the ``users`` table.

Password material never leaves this module inside a model object -- the
``User`` dataclass has no field to carry it. The one method that does return a
hash, :meth:`credentials_for_email`, is called by the auth service and nowhere
else.
"""

import sqlite3

from ..models import User

_USER_COLUMNS = "user_id, full_name, email"


class UserRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def get(self, user_id: str) -> User | None:
        row = self._connection.execute(
            f"SELECT {_USER_COLUMNS} FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return User.from_row(row) if row else None

    def exists(self, user_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row is not None

    def credentials_for_email(self, email: str) -> tuple[str, str, str] | None:
        """Return (user_id, password_hash, password_salt) for a login attempt.

        Accounts seeded from the sample data have no password set; they are
        filtered out here so that "no credentials" and "wrong password" are
        indistinguishable to the caller.
        """
        row = self._connection.execute(
            """
            SELECT user_id, password_hash, password_salt
              FROM users
             WHERE email = ? COLLATE NOCASE
               AND password_hash IS NOT NULL
            """,
            (email.strip(),),
        ).fetchone()
        if row is None:
            return None
        return row["user_id"], row["password_hash"], row["password_salt"]

    def email_taken(self, email: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE", (email.strip(),)
        ).fetchone()
        return row is not None

    def next_user_id(self) -> str:
        """Allocate the next USR##### identifier.

        Runs inside the caller's write transaction, so the read and the insert
        that follows it cannot interleave with a competing registration.
        """
        row = self._connection.execute(
            """
            SELECT MAX(CAST(SUBSTR(user_id, 4) AS INTEGER)) AS highest
              FROM users
             WHERE user_id LIKE 'USR%'
            """
        ).fetchone()
        return "USR%05d" % ((row["highest"] or 0) + 1)

    def create(
        self,
        *,
        user_id: str,
        full_name: str,
        email: str,
        password_hash: str,
        password_salt: str,
        signup_date: str,
    ) -> User:
        self._connection.execute(
            """
            INSERT INTO users (user_id, full_name, email, phone,
                               signup_date, password_hash, password_salt)
            VALUES (?, ?, ?, '', ?, ?, ?)
            """,
            (user_id, full_name, email, signup_date, password_hash, password_salt),
        )
        return User(user_id=user_id, full_name=full_name, email=email)

    def set_password(self, user_id: str, password_hash: str, password_salt: str) -> None:
        self._connection.execute(
            "UPDATE users SET password_hash = ?, password_salt = ? WHERE user_id = ?",
            (password_hash, password_salt, user_id),
        )

    def list_with_login(self, limit: int) -> list[User]:
        """Accounts that can actually be signed into -- surfaced as demo hints."""
        rows = self._connection.execute(
            f"""
            SELECT {_USER_COLUMNS}
              FROM users
             WHERE password_hash IS NOT NULL
             ORDER BY user_id
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [User.from_row(row) for row in rows]

    def upsert_many(self, users: list[dict]) -> int:
        """Bulk-load users during seeding, leaving any existing password intact."""
        self._connection.executemany(
            """
            INSERT INTO users (user_id, full_name, email, phone, signup_date)
            VALUES (:user_id, :full_name, :email, :phone, :signup_date)
            ON CONFLICT (user_id) DO UPDATE SET
                full_name   = excluded.full_name,
                email       = excluded.email,
                phone       = excluded.phone,
                signup_date = excluded.signup_date
            """,
            users,
        )
        return len(users)
