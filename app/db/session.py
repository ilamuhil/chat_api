from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.core.env import load_app_env

logger = logging.getLogger(__name__)

load_app_env()


def _require_env(key: str) -> str:
    v = os.getenv(key)
    if v is None or not str(v).strip():
        raise RuntimeError(f"Environment variable {key} is not set")
    return str(v).strip()


def _build_postgres_url(
    host: str, port: str, user: str, password: str, name: str
) -> str:
    # URL-encode password so special characters don't break the URL.
    return (
        f"postgresql+psycopg://{user}:{quote_plus(password)}@{host}:{port}/{name}"
        f"?sslmode=require"
    )


### Dashboard database connection

dashboard_db_URL: str | None = None
dashboard_db_engine = None
DashboardDbSessionLocal = None
AsyncDashboardDbSessionLocal = None
async_dashboard_db_engine = None

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
        pool_recycle=300,
    )
    async_dashboard_db_engine = create_async_engine(
        dashboard_db_URL,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=300,
    )
    AsyncDashboardDbSessionLocal = async_sessionmaker(
        async_dashboard_db_engine, expire_on_commit=False
    )
    DashboardDbSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=dashboard_db_engine
    )
except Exception as e:
    logger.exception("Error configuring dashboard database", exc_info=e)
    pass


#### Python chat database connection

PYTHON_CHAT_DATABASE_URL: str | None = None
chat_engine = None
SessionLocal = None
AsyncSessionLocal = None
async_chat_engine = None
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
        pool_recycle=300,
    )
    async_chat_engine = create_async_engine(
        PYTHON_CHAT_DATABASE_URL,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=300,
    )
    AsyncSessionLocal = async_sessionmaker(async_chat_engine, expire_on_commit=False)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=chat_engine)
except Exception as e:
    # Engine/session will be unavailable until env vars are set.
    logger.exception("Error configuring python chat database", exc_info=e)
    pass


def create_dashboard_db_session() -> Session:
    if DashboardDbSessionLocal is None:
        raise RuntimeError(
            "Public DB is not configured (DASHBOARD_DB_* env vars missing)."
        )
    return DashboardDbSessionLocal()


def create_async_dashboard_db_session() -> AsyncSession:
    if AsyncDashboardDbSessionLocal is None:
        raise RuntimeError(
            "Public DB is not configured (DASHBOARD_DB_* env vars missing)."
        )
    return AsyncDashboardDbSessionLocal()


def create_async_chat_db_session() -> AsyncSession:
    if AsyncSessionLocal is None:
        raise RuntimeError(
            "Python chat DB is not configured (CHAT_DB_* env vars missing)."
        )
    return AsyncSessionLocal()


def create_chat_db_session() -> Session:
    if SessionLocal is None:
        raise RuntimeError(
            "Python chat DB is not configured (CHAT_DB_* env vars missing)."
        )
    return SessionLocal()


async def get_async_dashboard_db() -> AsyncGenerator[AsyncSession, None]:
    async with create_async_dashboard_db_session() as session:
        yield session


async def get_async_chat_db() -> AsyncGenerator[AsyncSession, None]:
    async with create_async_chat_db_session() as session:
        yield session


def get_dashboard_db():
    with create_dashboard_db_session() as session:
        yield session


def get_chat_db():
    with create_chat_db_session() as session:
        yield session


def ping(engine) -> int:
    if engine is None:
        raise RuntimeError("Engine is not configured.")
    with engine.connect() as conn:
        return conn.execute(text("SELECT 1")).scalar_one()
