from __future__ import annotations

import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.env import load_app_env


load_app_env()


def _require_env(keys: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in keys:
        v = os.getenv(k)
        if v is None or not str(v).strip():
            raise Exception(f"Environment variable {k} is not set")
        out[k] = str(v).strip()
    return out


def _build_url(host: str, port: str, user: str, password: str, name: str) -> str:
    # URL-encode password so special characters don't break the URL.
    return (
        f"postgresql+psycopg://{user}:{quote_plus(password)}@{host}:{port}/{name}"
        f"?sslmode=require"
    )


# Local / python_chat DB (required in production, but keep imports safe if env is missing)
PYTHON_CHAT_DB_URL: str | None = None
python_chat_engine = None
SessionLocal = None

try:
    DB_ENV = _require_env(["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"])
    PYTHON_CHAT_DB_URL = _build_url(
        DB_ENV["DB_HOST"],
        DB_ENV["DB_PORT"],
        DB_ENV["DB_USER"],
        DB_ENV["DB_PASSWORD"],
        DB_ENV["DB_NAME"],
    )
    python_chat_engine = create_engine(
        PYTHON_CHAT_DB_URL, echo=True, pool_size=10, max_overflow=20, pool_pre_ping=True
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=python_chat_engine)
except Exception:
    # Engine/session will be unavailable until env vars are set.
    pass


# Supabase DB (optional)
SUPABASE_DB_URL: str | None = None
supabase_engine = None
SupabaseSessionLocal = None

_supabase_keys = ["SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_USER", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_DB_NAME"]
if all(os.getenv(k) for k in _supabase_keys):
    SB = _require_env(_supabase_keys)
    SUPABASE_DB_URL = _build_url(
        SB["SUPABASE_DB_HOST"],
        SB["SUPABASE_DB_PORT"],
        SB["SUPABASE_DB_USER"],
        SB["SUPABASE_SERVICE_ROLE_KEY"],
        SB["SUPABASE_DB_NAME"],
    )
    supabase_engine = create_engine(
        SUPABASE_DB_URL, echo=True, pool_size=10, max_overflow=20, pool_pre_ping=True
    )
    SupabaseSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=supabase_engine)


def get_db():
    if SessionLocal is None:
        raise RuntimeError("Local DB is not configured (DB_* env vars missing).")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_supabase_db():
    if SupabaseSessionLocal is None:
        raise RuntimeError("Supabase DB is not configured (SUPABASE_DB_* env vars missing).")
    db = SupabaseSessionLocal()
    try:
        yield db
    finally:
        db.close()


def ping(engine) -> int:
    if engine is None:
        raise RuntimeError("Engine is not configured.")
    with engine.connect() as conn:
        return conn.execute(text("SELECT 1")).scalar_one()


