"""Adversarial regression test suite for Task D010 (HTTP/SDK/MCP Temporal Parity),
Task D011 (Bounded Long-Lived Runtime State), and Task D012 (Release/Runtime Hygiene).
"""

import asyncio
from pathlib import Path

import pytest

from mesa_client.client import AsyncMesaV4Client, MesaV4Client
from mesa_mcp.bounded_cache import BoundedAsyncKeyedLocks, BoundedLRUCache
from mesa_mcp.errors import MCPError
from mesa_mcp.gateway.operations import CircuitBreaker


def test_d010_temporal_parity_sdk_signatures():
    """Verify that sync and async SDK search & get_context expose valid_at, valid_from, and valid_to."""
    sync_search_params = MesaV4Client.search.__code__.co_varnames
    async_search_params = AsyncMesaV4Client.search.__code__.co_varnames
    sync_ctx_params = MesaV4Client.get_context.__code__.co_varnames
    async_ctx_params = AsyncMesaV4Client.get_context.__code__.co_varnames

    for param in ("valid_at", "valid_from", "valid_to"):
        assert param in sync_search_params
        assert param in async_search_params
        assert param in sync_ctx_params
        assert param in async_ctx_params


def test_d010_sync_sdk_serializes_temporal_filters(monkeypatch):
    calls = []
    client = object.__new__(MesaV4Client)

    def capture(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {}

    monkeypatch.setattr(client, "_request", capture)
    filters = {
        "valid_at": "2026-08-14T10:00:00Z",
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": "2026-12-31T23:59:59Z",
    }
    client.search(session_id="session", query="query", **filters)
    client.get_context(session_id="session", **filters)

    assert filters.items() <= calls[0][2]["json"].items()
    assert filters.items() <= calls[1][2]["params"].items()


@pytest.mark.asyncio
async def test_d010_async_sdk_serializes_temporal_filters(monkeypatch):
    calls = []
    client = object.__new__(AsyncMesaV4Client)

    async def capture(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {}

    monkeypatch.setattr(client, "_request", capture)
    filters = {
        "valid_at": "2026-08-14T10:00:00Z",
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": "2026-12-31T23:59:59Z",
    }
    await client.search(session_id="session", query="query", **filters)
    await client.get_context(session_id="session", **filters)

    assert filters.items() <= calls[0][2]["json"].items()
    assert filters.items() <= calls[1][2]["params"].items()


def test_d011_bounded_cache_eviction_and_ttl():
    """Verify that BoundedLRUCache enforces max_size via LRU eviction and prunes expired TTL items."""
    cache = BoundedLRUCache[str, str](max_size=3, default_ttl_seconds=0.1)

    # Insert 3 items
    cache.put("k1", "v1")
    cache.put("k2", "v2")
    cache.put("k3", "v3")

    assert len(cache) == 3
    assert cache.get("k1") == "v1"

    # Insert 4th item -> k2 should be evicted (k1 was accessed recently, k2 is oldest)
    cache.put("k4", "v4")

    assert len(cache) == 3
    assert cache.get("k2") is None  # Evicted!
    assert cache.get("k4") == "v4"

    # Wait for TTL expiration
    import time

    time.sleep(0.15)

    assert cache.get("k1") is None  # Expired!
    assert len(cache) == 0


@pytest.mark.asyncio
async def test_d011_session_locks_never_evict_an_active_scope():
    locks = BoundedAsyncKeyedLocks[str](max_size=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_first():
        async with locks.hold("scope-a"):
            entered.set()
            await release.wait()

    task = asyncio.create_task(hold_first())
    await entered.wait()
    with pytest.raises(RuntimeError, match="capacity exhausted"):
        async with locks.hold("scope-b"):
            pass
    release.set()
    await task
    assert len(locks) == 0
    async with locks.hold("scope-b"):
        assert len(locks) == 1
    assert len(locks) == 0


@pytest.mark.asyncio
async def test_d011_cancelled_session_lock_waiter_is_pruned():
    locks = BoundedAsyncKeyedLocks[str](max_size=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_first():
        async with locks.hold("scope"):
            entered.set()
            await release.wait()

    async def wait_for_same_scope():
        async with locks.hold("scope"):
            pass

    holder = asyncio.create_task(hold_first())
    await entered.wait()
    waiter = asyncio.create_task(wait_for_same_scope())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await holder
    assert len(locks) == 0


@pytest.mark.asyncio
async def test_circuit_breaker_preserves_retryable_backend_error():
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=60)

    async def fail():
        raise MCPError("BACKEND_UNAVAILABLE", "backend failed", retryable=True)

    with pytest.raises(MCPError, match="backend failed"):
        await breaker.call(fail)
    assert breaker.state == "OPEN"


def test_d012_release_hygiene_checks():
    """Verify direct dependencies and the shipped image's release gates."""
    root = Path(__file__).parents[1]
    content = (root / "pyproject.toml").read_text(encoding="utf-8")

    # Verify key runtime dependencies are declared directly
    for dep in (
        "aiosqlite",
        "fastapi",
        "uvicorn",
        "lancedb",
        "pydantic",
        "alembic",
        "httpx",
    ):
        assert dep in content

    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "mesa-memory:security" in workflow
    assert "image-ref: mesa-memory:security" in workflow
    assert "mesa-runtime-image.cdx.json" in workflow

    launcher = (root / "scripts/run_server.py").read_text(encoding="utf-8")
    assert "from mesa_memory.api import server as _server" in launcher
    assert "app = _server.app" in launcher
    assert "MemoryDAO(" not in launcher

    schemas = (root / "mesa_api/schemas.py").read_text(encoding="utf-8")
    assert "Fused relevance score (higher is better)" in schemas
