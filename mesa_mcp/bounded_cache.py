"""Bounded LRU and TTL state container for process-level caches/maps (Task D011)."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Any, AsyncIterator, Generic, TypeVar

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
        self._lock = RLock()

    def get(self, key: KeyT, default: ValT | None = None) -> ValT | None:
        with self._lock:
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
        with self._lock:
            self._prune_expired()
            ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
            expires_at = (time.monotonic() + ttl) if ttl is not None else None
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (value, expires_at)
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def setdefault(
        self, key: KeyT, default_factory: Any, ttl_seconds: float | None = None
    ) -> ValT:
        with self._lock:
            self._prune_expired()
            existing = self._cache.get(key)
            if existing is not None:
                self._cache.move_to_end(key)
                return existing[0]
            val = default_factory() if callable(default_factory) else default_factory
            ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
            expires_at = (time.monotonic() + ttl) if ttl is not None else None
            self._cache[key] = (val, expires_at)
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
            return val

    def pop(self, key: KeyT, default: ValT | None = None) -> ValT | None:
        with self._lock:
            if key in self._cache:
                val, _ = self._cache.pop(key)
                return val
            return default

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        with self._lock:
            self._prune_expired()
            return len(self._cache)

    def _prune_expired(self) -> None:
        now = time.monotonic()
        expired = [
            k for k, (_, exp) in self._cache.items() if exp is not None and exp <= now
        ]
        for k in expired:
            del self._cache[k]


@dataclass
class _AsyncLockEntry:
    lock: asyncio.Lock
    users: int = 0


class BoundedAsyncKeyedLocks(Generic[KeyT]):
    """Bounded keyed async locks that never evict a holder or waiter."""

    def __init__(self, max_size: int = 512) -> None:
        if max_size < 1:
            raise ValueError("max_size must be at least 1")
        self._max_size = max_size
        self._entries: dict[KeyT, _AsyncLockEntry] = {}
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def hold(self, key: KeyT) -> AsyncIterator[None]:
        async with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                if len(self._entries) >= self._max_size:
                    raise RuntimeError("session lock capacity exhausted")
                entry = _AsyncLockEntry(asyncio.Lock())
                self._entries[key] = entry
            entry.users += 1
        acquired = False
        try:
            await entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            async with self._guard:
                entry.users -= 1
                if entry.users == 0 and self._entries.get(key) is entry:
                    del self._entries[key]

    def __len__(self) -> int:
        return len(self._entries)
