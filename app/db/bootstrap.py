"""Create the chat database schema from the SQLAlchemy models.

This module is intentionally separate from application startup. Database
initialization should be an explicit operational action, not something that
every API process attempts during startup.

The initializer is idempotent for missing tables: SQLAlchemy creates tables
that do not exist and leaves existing tables unchanged. It does not perform
schema migrations; use Alembic for changes to an existing schema.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable

from sqlalchemy import Engine, inspect, text

from app.db.session import chat_engine
from app.models.chat_db_models import Base


REQUIRED_EXTENSIONS = ("vector", "pgcrypto")


def ensure_postgres_extensions(engine: Engine) -> None:
    """Ensure PostgreSQL extensions required by the chat models exist."""
    with engine.begin() as connection:
        for extension in REQUIRED_EXTENSIONS:
            connection.execute(
                text(f"CREATE EXTENSION IF NOT EXISTS {extension}")
            )


def existing_chat_tables(engine: Engine) -> set[str]:
    """Return chat tables currently present in the database schema."""
    return set(inspect(engine).get_table_names())


def create_chat_tables(
    engine: Engine,
    *,
    ensure_extensions: bool = True,
) -> tuple[str, ...]:
    """Create missing chat tables and return the tables created.

    This function does not drop tables or alter existing columns. It is safe
    to call repeatedly for local setup and for provisioning an empty database.
    """
    if ensure_extensions:
        ensure_postgres_extensions(engine)

    before = existing_chat_tables(engine)
    Base.metadata.create_all(bind=engine, checkfirst=True)
    after = existing_chat_tables(engine)
    return tuple(sorted(after - before))


def _format_tables(tables: Iterable[str]) -> str:
    return ", ".join(tables) if tables else "none"


def main() -> int:
    """Run the chat database bootstrap command."""
    parser = argparse.ArgumentParser(
        description="Create missing Chat API tables from SQLAlchemy models."
    )
    parser.add_argument(
        "--skip-extensions",
        action="store_true",
        help="Do not create the vector and pgcrypto PostgreSQL extensions.",
    )
    args = parser.parse_args()

    if chat_engine is None:
        raise RuntimeError(
            "Chat DB is not configured. Set CHAT_DB_* variables in .env.local."
        )

    created = create_chat_tables(
        chat_engine,
        ensure_extensions=not args.skip_extensions,
    )
    print(f"Chat database initialized. Created tables: {_format_tables(created)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
