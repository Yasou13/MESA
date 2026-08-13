"""Adversarial regression test suite for Task D010 (HTTP/SDK/MCP Temporal Parity),
Task D011 (Bounded Long-Lived Runtime State), and Task D012 (Release/Runtime Hygiene)."""

import pytest
import asyncio
from mesa_client.client import MesaV4Client, AsyncMesaV4Client
from mesa_mcp.bounded_cache import BoundedLRUCache
from mesa_mcp.v4_service import MesaHttpV4Service
from mesa_mcp.configuration import MCPSettings


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


def test_d012_release_hygiene_checks():
    """Verify release hygiene contracts: directly imported dependencies present in pyproject.toml."""
    pyproject_path = "/home/yasin/Desktop/MESA/pyproject.toml"
    with open(pyproject_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Verify key runtime dependencies are declared directly
    for dep in ("aiosqlite", "fastapi", "uvicorn", "lancedb", "pydantic", "alembic", "httpx"):
        assert dep in content
