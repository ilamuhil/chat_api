"""Redis-backed store for chat session metadata. WebSockets stay in process memory."""

from __future__ import annotations

import json
from typing import Any, cast

from app.infra.redis_client import redis_client

_KEY_PREFIX = "data:store:"
_DEFAULT_TTL = 3600  # 1 hour


def _key(key: str) -> str:
    return f"{_KEY_PREFIX}{key}"


def set_data(key: str, metadata: dict[str, Any], ttl: int = _DEFAULT_TTL) -> None:
    """Store json data in Redis. Overwrites if exists."""
    key = _key(key)
    redis_client.setex(key, ttl, json.dumps(metadata))


def get_data(key: str) -> dict[str, Any] | None:
    """Get data metadata from Redis, or None if missing/expired."""
    key = _key(key)
    raw = cast(str | None, redis_client.get(key))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def delete_data(key: str) -> None:
    """Remove data from Redis."""
    redis_client.delete(_key(key))


def data_exists(key: str) -> bool:
    """Check if data metadata exists in Redis."""
    return cast(int, redis_client.exists(_key(key))) > 0
