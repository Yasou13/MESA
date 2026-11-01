"""Query providers must overlap without holding the shared search connection."""

import asyncio
import time

import pytest

from mesa_storage.dao import MemoryDAO
from tests.test_p702_search_scope_cache import _search, _seed
from tests.test_v4_bm25_scope_isolation import _environment
from tests.test_v4_retrieval_hardening_phase1 import TEST_EMBEDDING_IDENTITY


@pytest.mark.asyncio
@pytest.mark.parametrize("cache", [True, False])
async def test_eight_slow_query_embeddings_overlap_and_preserve_results(tmp_path, cache):
    sql, vector, graph, _dao = await _environment(tmp_path)
    vector.embedding_identity = TEST_EMBEDDING_IDENTITY
    dao = MemoryDAO(sql, vector, graph, search_scope_cache_enabled=cache)
    held = []

    async def slow_embedding(query):
        held.append(sql._search_lock.locked())
        await asyncio.sleep(0.3)
        return [1.0, 0.0]

    async def vector_search(query_vector, *, allowed_node_ids, **kwargs):
        assert query_vector == [1.0, 0.0]
        return [
            {"node_id": node, "_distance": i / 10}
            for i, node in enumerate(sorted(allowed_node_ids))
        ]

    vector.compute_query_embedding = slow_embedding
    vector.search = vector_search
    try:
        await _seed(dao, 1)
        await _seed(dao, 2)
        await _seed(dao, 3, tenant="other")
        queries = [(f"kira bedeli {i}", "t" if i % 2 == 0 else "other") for i in range(8)]

        async def search(query, tenant):
            return await _search(dao, query, tenant_id=tenant, graph_enabled=False)

        expected = [await search(*query) for query in queries]
        assert all(expected)
        held.clear()
        start = time.perf_counter()
        results = await asyncio.gather(*(search(*query) for query in queries))
        elapsed = time.perf_counter() - start
        print(f"eight searches cache={cache}: {elapsed:.6f}s; provider lock states={held}")
        assert results == expected
        assert len(held) == 8
        assert elapsed < 1.2
        assert not any(held)
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_incompatible_vectors_keep_bm25_when_query_provider_is_unavailable(tmp_path):
    sql, vector, graph, dao = await _environment(tmp_path)
    try:
        # This fixture has vector artifacts but no compatible runtime identity.
        await _seed(dao, 1)
        expected = await _search(dao)

        async def unavailable(query):
            raise RuntimeError("provider unavailable")

        vector.compute_query_embedding = unavailable
        assert await _search(dao) == expected
    finally:
        await sql.close()
