"""Redis-backed store for chat session metadata. WebSockets stay in process memory."""

from __future__ import annotations

import json
from typing import Any, cast

from app.infra.redis_client import redis_client

_KEY_PREFIX = "data:store:"
_DEFAULT_TTL = 3600  # 1 hour

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None


def _key(key: str) -> str:
    return f"{_KEY_PREFIX}{key}"


def set_data(key: str, value: JsonValue, ttl: int | None = _DEFAULT_TTL) -> None:
    """Store JSON-serializable data in Redis. Pass ttl=None to persist without expiry."""
    redis_key = _key(key)
    payload = json.dumps(value)
    if ttl is None:
        redis_client.set(redis_key, payload)
    else:
        redis_client.setex(redis_key, ttl, payload)


def get_data(key: str) -> JsonValue | None:
    """Get JSON data from Redis, or None if missing/expired."""
    raw = cast(str | None, redis_client.get(_key(key)))
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
