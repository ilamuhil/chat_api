from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path
from urllib.parse import quote_plus

from alembic import context
from sqlalchemy import create_engine, pool

# Alembic Config object
config = context.config

# Configure Python logging from ini (if present)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# Ensure project root is on sys.path so `import app...` works
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _require_env(key: str) -> str:
    v = os.getenv(key)
    if v is None or not str(v).strip():
        raise RuntimeError(f"Environment variable {key} is not set")
    return str(v).strip()

def _require_any_env(keys: list[str]) -> str:
    """
    Return the first non-empty env var from `keys`.
    Useful while transitioning env names (e.g. CHAT_DB_* -> PYTHON_CHAT_DB_*).
    """
    for k in keys:
        v = os.getenv(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    raise RuntimeError(f"None of these environment variables are set: {', '.join(keys)}")


def _build_postgres_url(host: str, port: str, user: str, password: str, name: str) -> str:
    # SQLAlchemy psycopg3 URL; require TLS.
    return (
        f"postgresql+psycopg://{user}:{quote_plus(password)}@{host}:{port}/{name}"
        f"?sslmode=require"
    )


# Load .env/.env.local for local development
from app.core.env import load_app_env  # noqa: E402

load_app_env()

# Target metadata: ONLY the chat DB models
from app.models.chat_db_models import Base  # noqa: E402

target_metadata = Base.metadata


def get_url() -> str:
    # Prefer PYTHON_CHAT_DB_* (new), fall back to CHAT_DB_* (legacy/current .env.local).
    host = _require_any_env(["PYTHON_CHAT_DB_HOST", "CHAT_DB_HOST"])
    port = _require_any_env(["PYTHON_CHAT_DB_PORT", "CHAT_DB_PORT"])
    user = _require_any_env(["PYTHON_CHAT_DB_USERNAME", "CHAT_DB_USERNAME"])
    password = _require_any_env(["PYTHON_CHAT_DB_PASSWORD", "CHAT_DB_PASSWORD"])
    name = _require_any_env(["PYTHON_CHAT_DB_NAME", "CHAT_DB_NAME"])
    return _build_postgres_url(host, port, user, password, name)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(get_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
