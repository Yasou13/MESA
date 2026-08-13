"""Bounded LRU and TTL state container for process-level caches/maps (Task D011)."""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Generic, TypeVar

KeyT = TypeVar("KeyT")
ValT = TypeVar("ValT")


class BoundedLRUCache(Generic[KeyT, ValT]):
    """Thread/Async-safe bounded LRU and TTL dictionary."""

    def __init__(self, max_size: int = 512, default_ttl_seconds: float | None = None):
        if max_size < 1:
            raise ValueError("max_size must be at least 1")
        self._max_size = max_size
        self._default_ttl = default_ttl_seconds
        self._cache: OrderedDict[KeyT, tuple[ValT, float | None]] = OrderedDict()

    def get(self, key: KeyT, default: ValT | None = None) -> ValT | None:
        self._prune_expired()
        if key not in self._cache:
            return default
        val, expires_at = self._cache[key]
        if expires_at is not None and expires_at <= time.monotonic():
            del self._cache[key]
            return default
        self._cache.move_to_end(key)
        return val

    def put(self, key: KeyT, value: ValT, ttl_seconds: float | None = None) -> None:
        self._prune_expired()
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        expires_at = (time.monotonic() + ttl) if ttl is not None else None
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = (value, expires_at)
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def setdefault(self, key: KeyT, default_factory: Any, ttl_seconds: float | None = None) -> ValT:
        existing = self.get(key)
        if existing is not None:
            return existing
        val = default_factory() if callable(default_factory) else default_factory
        self.put(key, val, ttl_seconds=ttl_seconds)
        return val

    def pop(self, key: KeyT, default: ValT | None = None) -> ValT | None:
        if key in self._cache:
            val, _ = self._cache.pop(key)
            return val
        return default

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        self._prune_expired()
        return len(self._cache)

    def _prune_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._cache.items() if exp is not None and exp <= now]
        for k in expired:
            del self._cache[k]
