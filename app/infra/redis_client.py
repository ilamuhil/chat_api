from __future__ import annotations

import os

from redis import Redis


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def get_redis() -> Redis:
    """Create a Redis client. Connections are opened lazily on first command."""
    return Redis.from_url(REDIS_URL, decode_responses=True)


# Optional shared client for app usage.
redis_client = get_redis()


if __name__ == "__main__":
    c = get_redis()
    c.set("foo", "bar")
    print(c.get("foo"))
    c.hset(
        "user-session:123",
        mapping={"name": "John", "surname": "Smith", "company": "Redis", "age": 29},
    )
    print(c.hgetall("user-session:123"))

