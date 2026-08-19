"""Runtime configuration.

Every knob is settable three ways, in ascending priority: the defaults
below, a ``BOXOFFICE_*`` environment variable, then an explicit CLI flag.
That ordering is what lets the same code run from ``python app.py`` during
development and from a container with nothing but environment variables.
"""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Session lifetime. Long enough that a demo or a defence session never logs
# you out mid-click, short enough that an abandoned browser does not stay
# authenticated forever.
SESSION_TTL_SECONDS = 12 * 60 * 60

# PBKDF2-HMAC-SHA256 work factor. Raising this makes both login and account
# creation proportionally slower; it is the only defence that matters if the
# database file is ever leaked.
PASSWORD_HASH_ITERATIONS = 210_000


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AppConfig:
    """Resolved settings for one running instance."""

    database_path: Path
    seed_data_dir: Path
    storage_root: str
    export_enabled: bool
    host: str
    port: int
    debug: bool

    @classmethod
    def from_environment(cls) -> "AppConfig":
        return cls(
            database_path=_env_path("BOXOFFICE_DATABASE", PROJECT_ROOT / "instance" / "boxoffice.db"),
            seed_data_dir=_env_path("BOXOFFICE_SEED_DATA", PROJECT_ROOT / "data"),
            storage_root=os.environ.get("BOXOFFICE_STORAGE_ROOT", str(PROJECT_ROOT / "storage_output")),
            export_enabled=_env_flag("BOXOFFICE_EXPORT", True),
            host=os.environ.get("BOXOFFICE_HOST", "127.0.0.1"),
            port=int(os.environ.get("BOXOFFICE_PORT", "5000")),
            debug=_env_flag("BOXOFFICE_DEBUG", False),
        )

    def merged_with(self, **overrides) -> "AppConfig":
        """Return a copy with any non-``None`` override applied.

        Called with the parsed CLI namespace, where unset flags are ``None``
        and must not clobber an environment variable.
        """
        supplied = {key: value for key, value in overrides.items() if value is not None}
        for key in ("database_path", "seed_data_dir"):
            if key in supplied:
                supplied[key] = Path(supplied[key]).expanduser()
        return AppConfig(**{**self.__dict__, **supplied})
