"""Small process-local LRU caches; keys must include tenant/bot/version scope."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from time import monotonic
from typing import Any


class TTLCache:
    def __init__(self, max_entries: int, ttl_seconds: float):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._values: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()

    def get(self, key: tuple):
        entry = self._values.get(key)
        if entry is None:
            return None
        expires, value = entry
        if monotonic() >= expires:
            del self._values[key]
            return None
        self._values.move_to_end(key)
        return deepcopy(value)

    def put(self, key: tuple, value: Any) -> None:
        self._values[key] = (monotonic() + self.ttl_seconds, deepcopy(value))
        self._values.move_to_end(key)
        while len(self._values) > self.max_entries:
            self._values.popitem(last=False)

    def clear(self) -> None:
        self._values.clear()

    def invalidate_bot(self, bot_id: str) -> None:
        for key in list(self._values):
            if bot_id in key:
                del self._values[key]


configuration_cache = TTLCache(max_entries=128, ttl_seconds=30)
embedding_cache = TTLCache(max_entries=128, ttl_seconds=600)
