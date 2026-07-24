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

    # Project root (two levels up from app/core/env.py -> app -> project root)
    root = Path(__file__).resolve().parents[2]
    env_path = root / ".env"
    env_local_path = root / ".env.local"

    def _safe_load(path: Path, override: bool) -> None:
        # override false => do not override values from os.environ if it is present
        # override true => prioritize values from the dotenv file over os.environ
        try:
            load_dotenv(dotenv_path=path, override=override)
        except PermissionError:
            return

    if app_env in ("prod", "production"):
        _safe_load(env_path, override=False)
        return "production"

    if env_local_path.exists():
        # if .env.local exists, load it and override values from os.environ
        _safe_load(env_local_path, override=True)
    else:
        # if .env.local does not exist, load .env and do not override values from os.environ
        _safe_load(env_path, override=False)
    return app_env
