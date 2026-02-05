from __future__ import annotations

import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.env import load_app_env


load_app_env()


def _require_env(key: str) -> str:
    v = os.getenv(key)
    if v is None or not str(v).strip():
        raise RuntimeError(f"Environment variable {key} is not set")
    return str(v).strip()

def _build_postgres_url(host: str, port: str, user: str, password: str, name: str) -> str:
    # URL-encode password so special characters don't break the URL.
    return (
        f"postgresql+psycopg://{user}:{quote_plus(password)}@{host}:{port}/{name}"
        f"?sslmode=require"
    )


### Dashboard database connection

dashboard_db_URL: str | None = None
dashboard_db_engine = None
DashboardDbSessionLocal = None

try:
    host = _require_env("DASHBOARD_DB_HOST")
    port = _require_env("DASHBOARD_DB_PORT")
    user = _require_env("DASHBOARD_DB_USERNAME")
    password = _require_env("DASHBOARD_DB_PASSWORD")
    name = _require_env("DASHBOARD_DB_NAME")

    dashboard_db_URL = _build_postgres_url(host, port, user, password, name)
    dashboard_db_engine = create_engine(
        dashboard_db_URL,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )
    DashboardDbSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=dashboard_db_engine
    )
except Exception:
    pass


#### Python chat database connection

PYTHON_CHAT_DATABASE_URL: str | None = None
chat_engine = None
SessionLocal = None

try:
    host = _require_env("CHAT_DB_HOST")
    port = _require_env("CHAT_DB_PORT")
    user = _require_env("CHAT_DB_USERNAME")
    password = _require_env("CHAT_DB_PASSWORD")
    name = _require_env("CHAT_DB_NAME")

    PYTHON_CHAT_DATABASE_URL = _build_postgres_url(host, port, user, password, name)
    chat_engine = create_engine(
        PYTHON_CHAT_DATABASE_URL,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=chat_engine
    )
except Exception:
    # Engine/session will be unavailable until env vars are set.
    pass


def get_dashboard_db():
    if DashboardDbSessionLocal is None:
        raise RuntimeError(
            "Public DB is not configured (DASHBOARD_DB_* env vars missing)."
        )
    db = DashboardDbSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_chat_db():
    if SessionLocal is None:
        raise RuntimeError("Python chat DB is not configured (CHAT_DB_* env vars missing).")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ping(engine) -> int:
    if engine is None:
        raise RuntimeError("Engine is not configured.")
    with engine.connect() as conn:
        return conn.execute(text("SELECT 1")).scalar_one()


