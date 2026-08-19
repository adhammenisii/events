"""Entry point for the Box Office booking service.

    python app.py                      # sensible defaults, first run seeds the database
    python app.py --port 8000 --debug
    python app.py --storage-root hdfs://localhost:9000/ticket_system

The application itself is assembled in :func:`boxoffice.web.create_app`; this
module only parses arguments and starts a server, so the same app object can
be handed to gunicorn/waitress in a real deployment:

    waitress-serve --port 5000 --call boxoffice.web:create_app
"""

import argparse
import logging
from pathlib import Path

from boxoffice.config import AppConfig
from boxoffice.errors import BoxOfficeError
from boxoffice.web import configure_logging, create_app

logger = logging.getLogger("boxoffice.app")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, dest="database_path",
                        help="SQLite file to use (created and seeded if absent).")
    parser.add_argument("--data-root", type=Path, dest="seed_data_dir",
                        help="Folder holding events.csv, seats.csv and users.csv.")
    parser.add_argument("--storage-root", dest="storage_root",
                        help="Where bookings are mirrored: a local folder, or "
                             "hdfs://namenode:9000/ticket_system for the Part 1 cluster.")
    parser.add_argument("--no-export", action="store_const", const=False, dest="export_enabled",
                        help="Skip mirroring to storage; the database is still authoritative.")
    parser.add_argument("--host", help="Interface to bind (default 127.0.0.1).")
    parser.add_argument("--port", type=int, help="Port to listen on (default 5000).")
    parser.add_argument("--debug", action="store_const", const=True, dest="debug",
                        help="Verbose logging and automatic reload.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    config = AppConfig.from_environment().merged_with(**vars(arguments))

    try:
        app = create_app(config)
    except BoxOfficeError as exc:
        configure_logging(config.debug)
        logger.error("Could not start: %s", exc.message)
        return 1

    print(f"\n  Box Office is running at http://{config.host}:{config.port}")
    print(f"  Database: {config.database_path}")
    print("  Press Ctrl+C to stop.\n")

    # threaded=True is what makes the concurrency behaviour observable: the
    # development server then handles overlapping requests on separate threads
    # instead of queueing them, so two people really can race for a seat.
    app.run(host=config.host, port=config.port, debug=config.debug, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
