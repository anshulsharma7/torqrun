"""Alembic wrapper usable from code, tests, Docker and the command line.

Migrations ship inside the package, so this works the same from a source checkout and from an
installed wheel inside a container::

    TORQRUN_DATABASE_URL=postgresql://... python -m torqrun_db.migrate upgrade
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection

from torqrun_db.engine import normalize_url

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if url is not None:
        # ConfigParser treats '%' as interpolation; URL-encoded passwords contain it.
        cfg.set_main_option("sqlalchemy.url", normalize_url(url).replace("%", "%%"))
    return cfg


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str) -> None:
    command.downgrade(alembic_config(url), revision)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m torqrun_db.migrate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("upgrade", help="apply migrations (default: to head)")
    up.add_argument("revision", nargs="?", default="head")
    down = sub.add_parser("downgrade", help="revert migrations to a revision")
    down.add_argument("revision")
    sub.add_parser("current", help="show the database's current revision")
    sub.add_parser("heads", help="show the latest revision shipped with this build")
    rev = sub.add_parser("revision", help="create a new migration (developers)")
    rev.add_argument("-m", "--message", required=True)
    rev.add_argument("--autogenerate", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.cmd == "heads":
        print(head_revision())
        return 0

    url = os.environ.get("TORQRUN_DATABASE_URL")
    if not url:
        print("TORQRUN_DATABASE_URL is not set", file=sys.stderr)
        return 2
    cfg = alembic_config(url)
    if args.cmd == "upgrade":
        command.upgrade(cfg, args.revision)
    elif args.cmd == "downgrade":
        command.downgrade(cfg, args.revision)
    elif args.cmd == "current":
        command.current(cfg, verbose=True)
    elif args.cmd == "revision":
        command.revision(cfg, message=args.message, autogenerate=args.autogenerate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
