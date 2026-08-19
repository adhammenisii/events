"""Application factory.

Everything the request handlers need is built once here and hung off
``app.extensions["boxoffice"]``. Routes reach it through
:func:`boxoffice.web.routes.api_helpers.services`, which keeps them free of
module-level globals and makes the whole application constructible against a
throwaway database in tests.
"""

import atexit
import logging
from dataclasses import dataclass

from flask import Flask

from ..config import AppConfig
from ..db import Database
from ..db.bootstrap import DEFAULT_DEMO_PASSWORD, ensure_ready
from ..export import StorageExporter, make_target
from ..services import AuthService, BookingService, CatalogService
from .errors import register_error_handlers
from .routes import BLUEPRINTS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceRegistry:
    """The wired-up application, assembled once per process."""

    db: Database
    auth: AuthService
    catalog: CatalogService
    booking: BookingService
    exporter: StorageExporter | None
    demo_password: str


def create_app(config: AppConfig | None = None) -> Flask:
    config = config or AppConfig.from_environment()
    configure_logging(config.debug)

    app = Flask(__name__, static_folder=None)
    app.config["boxoffice"] = config
    # Responses are JSON read by JavaScript, not by a human reading curl
    # output; sorting and pretty-printing them only inflates the payload.
    app.json.sort_keys = False

    db = Database(config.database_path)
    report = ensure_ready(db, config.seed_data_dir)
    if report is not None:
        logger.info("First run -- seeded %s", report)

    exporter = _build_exporter(db, config)
    app.extensions["boxoffice"] = ServiceRegistry(
        db=db,
        auth=AuthService(db),
        catalog=CatalogService(db),
        booking=BookingService(db, on_change=exporter.request_export if exporter else None),
        exporter=exporter,
        demo_password=DEFAULT_DEMO_PASSWORD,
    )

    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)
    register_error_handlers(app)

    # Expired rows would otherwise accumulate for as long as the process runs;
    # once at startup is enough for a session lifetime measured in hours.
    app.extensions["boxoffice"].auth.purge_expired_sessions()

    logger.info("Box Office ready -- database %s", config.database_path)
    return app


def _build_exporter(db: Database, config: AppConfig) -> StorageExporter | None:
    """Start the storage mirror, or carry on without it.

    A misconfigured export target must not stop the booking service from
    accepting bookings -- the database is the source of truth, and the mirror
    is a convenience for the batch jobs downstream.
    """
    if not config.export_enabled:
        logger.info("Storage export disabled.")
        return None
    try:
        exporter = StorageExporter(db, make_target(config.storage_root))
        exporter.start()
    except Exception:
        logger.warning(
            "Storage export could not start for root %r; bookings will still be "
            "recorded in the database.", config.storage_root, exc_info=True,
        )
        return None

    atexit.register(exporter.stop)
    return exporter


def configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Werkzeug logs every static asset request; useful when debugging, noise
    # the rest of the time.
    logging.getLogger("werkzeug").setLevel(logging.DEBUG if debug else logging.WARNING)


__all__ = ["ServiceRegistry", "create_app"]
