from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_app_env() -> str:
    """
    Loads environment variables from a dotenv file based on environment.

    Rules:
    - production -> load `.env`
    - development (default) -> load `.env.local` (fallback to `.env` if missing)

    Environment selector (first non-empty wins):
    - APP_ENV
    - ENV
    - FASTAPI_ENV
    """
    app_env = (
        os.getenv("APP_ENV")
        or os.getenv("ENV")
        or os.getenv("FASTAPI_ENV")
        or "development"
    ).strip().lower()

    root = Path(__file__).resolve().parent
    env_path = root / ".env"
    env_local_path = root / ".env.local"

    if app_env in ("prod", "production"):
        # Production loads only `.env`
        load_dotenv(dotenv_path=env_path, override=False)
        return "production"

    # Development loads `.env.local` (fallback to `.env` if `.env.local` is absent)
    if env_local_path.exists():
        load_dotenv(dotenv_path=env_local_path, override=True)
    else:
        load_dotenv(dotenv_path=env_path, override=False)
    return "development"





