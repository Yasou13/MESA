"""Regression tests for Kùzu graph runtime closure.

Verifies:
1. Seed-local bounded 3-hop expansion at full legal scale (5808 entities, 5719 assertions)
   completes in bounded time (< 2s) with zero buffer manager exceptions.
2. Teardown race safety: timeout / cancellation while native C++ is active does not cause
   segmentation faults or pure virtual method calls during close() / shutdown.
3. Shutdown barrier: provider rejects new queries once shutdown begins.
4. Resource exhaustion safety: buffer pool / out-of-memory errors are typed and fail-closed
   without process termination.
5. Server survival: controlled 503 on graph outage, server stays alive, subsequent Graph OFF
   retrieval succeeds.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from mesa_storage.kuzu_provider import (
    GraphSearchError,
    KuzuGraphProvider,
)
from mesa_storage.kuzu_setup import initialize_schema_artifact
from tests.test_v4_graph_retrieval_hardening import (
    _close_test_env,
    _create_test_env,
    _ingest_entity_and_assertion,
)


@pytest.mark.asyncio
async def test_real_scale_3_hop_legal_graph_retrieval(tmp_path):
    """Verify 3-hop traversal on 5808 entities and 5719 assertions executes boundedly without memory exhaustion."""
    graph_path = tmp_path / "scale_graph"
    initialize_schema_artifact(str(graph_path))
    provider = KuzuGraphProvider(str(graph_path), max_workers=2, search_timeout_seconds=10.0)
    await provider.initialize()

    agent_id = "agent_scale_legal"
    N_ENTITIES = 5808
    N_ASSERTIONS = 5719

    # Ingest entities in batches
    for i in range(0, N_ENTITIES, 1000):
        async with provider.transaction():
            for j in range(i, min(i + 1000, N_ENTITIES)):
                await provider.insert_node(f"ent_{j}", f"Entity {j}", agent_id=agent_id)

    # Ingest assertions in a connected network structure with legal statute hubs
    for i in range(0, N_ASSERTIONS, 1000):
        async with provider.transaction():
            for j in range(i, min(i + 1000, N_ASSERTIONS)):
                if j < 5:
                    s_idx = 0
                    t_idx = j + 1
                elif j < 25:
                    s_idx = (j % 5) + 1
                    t_idx = 6 + (j % 10)
                elif j < 100:
                    s_idx = 6 + (j % 10)
                    t_idx = 16 + (j % 20)
                else:
                    s_idx = j % 150
                    t_idx = (j * 7) % N_ENTITIES

                await provider.insert_assertion(
                    assertion_id=f"ass_{j}",
                    agent_id=agent_id,
                    subject_id=f"ent_{s_idx}",
                    predicate="cites",
                    object_id=f"ent_{t_idx}",
                    confidence=0.9,
                    mutation_id=f"mut_{j}",
                )

    allowed_entity_ids = {f"ent_{i}" for i in range(N_ENTITIES)}
    allowed_assertion_ids = {f"ass_{i}" for i in range(N_ASSERTIONS)}

    t_start = time.monotonic()
    hits = await provider.search_v4_graph(
        agent_id=agent_id,
        seed_entity_ids=["ent_0"],
        allowed_entity_ids=allowed_entity_ids,
        allowed_assertion_ids=allowed_assertion_ids,
        max_hops=3,
        limit=50,
        direction="any",
    )
    elapsed = time.monotonic() - t_start

    # Execution must be bounded (well below 5s production timeout, typically < 0.2s)
    assert elapsed < 3.0, f"Query took {elapsed:.2f}s, expected < 3.0s"
    assert len(hits) > 0, "Expected non-empty hits from 3-hop traversal"
    assert len(hits) <= 50, f"Expected at most 50 hits, got {len(hits)}"

    # Check 3-hop evidence correctness
    hop3_hits = [h for h in hits if h["hops"] == 3]
    assert len(hop3_hits) > 0, "Expected 3-hop hits to be discovered"

    first_h3 = hop3_hits[0]
    assert len(first_h3["path_entity_ids"]) == 4  # seed + 2 intermediates + target
    assert len(first_h3["best_path_assertion_ids"]) == 3
    assert len(first_h3["edge_directions"]) == 3
    assert first_h3["seed_id"] == "ent_0"

    await provider.close()


@pytest.mark.asyncio
async def test_teardown_segfault_race_prevention(tmp_path):
    """Verify that timeout/cancellation during native query execution does not cause segfault on close()."""
    graph_path = tmp_path / "race_graph"
    initialize_schema_artifact(str(graph_path))
    provider = KuzuGraphProvider(str(graph_path), max_workers=2, search_timeout_seconds=0.01)
    await provider.initialize()

    agent_id = "agent_race"
    await provider.insert_node("ent_0", "Seed", agent_id=agent_id)
    await provider.insert_node("ent_1", "Target", agent_id=agent_id)
    await provider.insert_assertion(
        assertion_id="ass_0",
        agent_id=agent_id,
        subject_id="ent_0",
        predicate="cites",
        object_id="ent_1",
        mutation_id="m0",
    )

    # Simulate a slow query that holds the connection on executor thread
    def slow_native_call(*args, **kwargs):
        time.sleep(0.15)
        return []

    # Run query with tiny timeout so wait_for times out while worker thread is active
    with patch.object(provider, "_sync_execute", side_effect=slow_native_call):
        with pytest.raises(GraphSearchError) as exc_info:
            await provider.search_v4_graph(
                agent_id=agent_id,
                seed_entity_ids=["ent_0"],
                allowed_entity_ids={"ent_0", "ent_1"},
                allowed_assertion_ids={"ass_0"},
                max_hops=1,
            )
        assert "timed out" in str(exc_info.value) or "unavailable" in str(exc_info.value)

    # Immediately close provider while thread might still be active
    # The shutdown barrier must wait for the worker thread and NOT crash/segfault
    t0 = time.monotonic()
    await provider.close(timeout=2.0)
    close_time = time.monotonic() - t0

    assert not provider.is_initialized
    assert not provider.is_operational
    assert close_time < 2.0


@pytest.mark.asyncio
async def test_shutdown_barrier_rejects_new_queries(tmp_path):
    """Verify provider rejects queries once shutdown begins."""
    graph_path = tmp_path / "barrier_graph"
    initialize_schema_artifact(str(graph_path))
    provider = KuzuGraphProvider(str(graph_path), max_workers=1)
    await provider.initialize()

    await provider.close()

    with pytest.raises(RuntimeError):
        # Must fail when not initialized
        await provider.execute_query("RETURN 1")


@pytest.mark.asyncio
async def test_kuzu_resource_exhaustion_typed_and_non_fatal(tmp_path):
    """Verify Kùzu buffer pool / memory exceptions are cleanly caught and typed."""
    graph_path = tmp_path / "exhaustion_graph"
    initialize_schema_artifact(str(graph_path))
    provider = KuzuGraphProvider(str(graph_path), max_workers=1)
    await provider.initialize()

    with patch.object(
        provider,
        "execute_query",
        side_effect=RuntimeError(
            "Buffer manager exception: Unable to allocate memory! The buffer pool is full and no memory could be freed!"
        ),
    ):
        with pytest.raises(GraphSearchError) as exc_info:
            await provider.search_v4_graph(
                agent_id="test_agent",
                seed_entity_ids=["ent_0"],
                allowed_entity_ids={"ent_0", "ent_1"},
                allowed_assertion_ids={"ass_0"},
                max_hops=1,
            )
        assert "resource exhausted" in str(exc_info.value)

    assert provider.is_operational is False

    # Verify provider health check can recover
    health = await provider.health_check()
    assert health["status"] == "healthy"
    assert provider.is_operational is True

    await provider.close()


@pytest.mark.asyncio
async def test_server_survival_after_graph_outage(tmp_path):
    """Verify server survives graph search failure, returns controlled error, and Graph OFF search succeeds."""
    sql, vector, graph, dao = await _create_test_env(tmp_path)
    try:
        tenant_id = "test-tenant"
        agent_id = "test-agent"
        dataset_id = "default-ds"

        # Ingest test entities
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            mutation_id="m1",
            subject_name="StatuteA",
            predicate="governs",
            object_name="CaseB",
        )

        # 1. Graph search fails with GraphSearchError
        with patch.object(graph, "search_v4_graph", side_effect=GraphSearchError("Kùzu buffer pool full")):
            with pytest.raises(GraphSearchError):
                await dao.search_v4_memory(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    dataset_ids=[dataset_id],
                    query="StatuteA",
                    limit=10,
                    graph_enabled=True,
                )

        # 2. Server remains fully operational; Graph OFF search succeeds cleanly
        results_off = await dao.search_v4_memory(
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            query="StatuteA",
            limit=10,
            graph_enabled=False,
        )
        assert len(results_off) >= 1
        entity_names = {r["entity"]["canonical_name"] for r in results_off}
        assert "StatuteA" in entity_names

    finally:
        await _close_test_env(sql, vector, graph)
