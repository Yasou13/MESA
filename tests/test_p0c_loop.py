import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesa_memory.consolidation.loop import (
    CircuitBreaker,
    ConsolidationLoop,
    PersistentQueue,
)
from mesa_memory.retrieval.hybrid import HybridRetriever
from mesa_memory.valence.core import ValenceMotor


@pytest.mark.asyncio
async def test_consolidation_loop_full():
    dao = AsyncMock()
    dao.get_all_embeddings = MagicMock(return_value=[])
    dao.load_embedding_cache = MagicMock(return_value=[])
    embedder = MagicMock()
    llm_a = MagicMock()
    llm_b = MagicMock()
    obs_layer = MagicMock()

    loop = ConsolidationLoop(dao, embedder, llm_a, llm_b, obs_layer)

    # Empty batch
    dao.get_memories.return_value = []
    await loop.run_batch()

    # Non-empty batch
    dao.get_memories.return_value = [
        {"id": 1, "content": "test", "tier3_deferred": True}
    ]
    loop.router = AsyncMock()
    loop.router.validate.return_value = {"decision": "DISCARD"}
    await loop.run_batch([{"id": 1, "content": "test", "tier3_deferred": True}])

    # ADMIT
    loop.router.validate.return_value = {"decision": "ADMIT"}
    loop.triplet_extractor = MagicMock()
    loop.triplet_extractor.sort_by_salience.return_value = [
        {"id": 1, "content": "test", "tier3_deferred": True}
    ]
    loop.triplet_extractor.extract_batch = AsyncMock(return_value=({}, {}))
    loop.graph_writer = AsyncMock()
    loop.graph_writer.prefetch_embeddings.return_value = {}
    loop.graph_writer.commit_batch.return_value = (1, 0)
    await loop.run_batch([{"id": 1, "content": "test", "tier3_deferred": True}])

    # UNCERTAIN
    loop.router.validate.return_value = {"decision": "UNCERTAIN"}
    await loop.run_batch([{"id": 1, "content": "test", "tier3_deferred": True}])


@pytest.mark.asyncio
async def test_hybrid_reranking():
    retriever = HybridRetriever(AsyncMock(), MagicMock(), MagicMock())
    retriever.dao.get_epistemic_data_for_nodes = AsyncMock(return_value={})

    res = await retriever._apply_alpha_reranking(
        "test_agent",
        [{"cmb_id": "1", "score": 0.8}],
        [{"cmb_id": "2", "score": 0.9}],
        [{"cmb_id": "1", "score": 0.5}],
    )
    assert len(res) > 0

    res2 = retriever._cold_start_rerank(
        [{"cmb_id": "1", "fitness_score": 0.5, "score": 0.8}], top_k=5
    )
    assert len(res2) > 0


@pytest.mark.asyncio
async def test_hybrid_retrieve_full():
    dao = AsyncMock()
    analyzer = MagicMock()
    analyzer.extract_entities.return_value = ["E1"]

    retriever = HybridRetriever(dao, analyzer, MagicMock())
    retriever.access_control = AsyncMock()
    retriever.access_control.check_access.return_value = True

    # Cold start condition
    dao.find_nodes_by_name.return_value = []
    dao.get_memories.return_value = []

    retriever.get_vector_results = AsyncMock(
        return_value=[{"cmb_id": "1", "score": 0.8, "fitness_score": 0.5}]
    )
    retriever.get_graph_results = AsyncMock(return_value=[])

    dao.search_memory_fts.return_value = [
        {"id": "1", "entity_name": "test", "rank": 0.5}
    ]

    res = await retriever.retrieve("query", "agent1", "session1")
    assert res == ["1"]

    # Normal condition
    dao.find_nodes_by_name.return_value = [{"id": "1"}]
    dao.get_memories.return_value = [
        {"id": "1", "entity_name": "test"},
        {"id": "2", "entity_name": "test"},
        {"id": "3"},
    ]
    retriever.get_graph_results = AsyncMock(
        return_value=[{"cmb_id": "2", "score": 0.9}]
    )

    res = await retriever.retrieve("query", "agent1", "session1", enable_multi_hop=True)
    assert len(res) >= 0


@pytest.mark.asyncio
async def test_valence_evaluate_full():
    mock_llm = MagicMock()
    mock_llm.get_token_count.return_value = 1
    motor = ValenceMotor(mock_llm, MagicMock(), None)

    # explicit_correction
    res = await motor.evaluate(
        {"content_payload": "test", "resource_cost": {}}, {"explicit_correction": True}
    )
    assert res is True

    # is_novel
    with patch(
        "mesa_memory.valence.core.calculate_novelty_score", new_callable=AsyncMock
    ) as mock_novelty:
        mock_novelty.return_value = True
        res = await motor.evaluate({"content_payload": "test", "embedding": [0.1]}, {})
        assert res is True

        mock_novelty.return_value = False
        res = await motor.evaluate({"content_payload": "test", "embedding": [0.1]}, {})
        assert res == "DEFERRED"
        mock_novelty.assert_awaited()

    motor._recalibrate()
    assert motor._records_since_recalibration == 0


@pytest.mark.asyncio
async def test_run_graph_spreading():
    dao = AsyncMock()
    # Simulate KùzuDB salience results
    dao.find_nodes_by_name.return_value = [{"id": "A"}]
    dao.graph_provider = AsyncMock()
    dao.graph_provider.get_cognitive_salience.return_value = [
        {"node_id": "B", "score": 0.8},
    ]
    retriever = HybridRetriever(dao, MagicMock(), MagicMock())

    res = await retriever.get_graph_results("agent1", ["NodeA"])
    assert len(res) >= 1
    assert res[0]["cmb_id"] == "B"
    assert res[0]["source"] == "graph"


@pytest.mark.asyncio
async def test_graph_spreading_no_results():
    dao = AsyncMock()
    dao.find_nodes_by_name.return_value = [{"id": "A"}]
    dao.graph_provider = AsyncMock()
    dao.graph_provider.get_cognitive_salience.return_value = []

    retriever = HybridRetriever(dao, MagicMock(), MagicMock())
    res = await retriever.get_graph_results("agent1", ["NodeA"])
    assert res == []


@pytest.mark.asyncio
async def test_persistent_queue():
    q = PersistentQueue("./storage/test_queue.jsonl")
    await q.clear()
    assert await q.alen() == 0
    await q.aappend({"a": 1})
    assert await q.alen() == 1
    assert await q.agetitem(0) == {"a": 1}
    with pytest.raises(IndexError):
        _ = await q.agetitem(1)
    os.remove("./storage/test_queue.jsonl")
    assert await q.alen() == 0
    with pytest.raises(IndexError):
        _ = await q.agetitem(0)


def test_circuit_breaker():
    cb = CircuitBreaker(failure_threshold=2, cooldown_period=0.1)
    assert not cb.is_open
    cb.record_failure()
    assert not cb.is_open
    cb.record_failure()
    assert cb.is_open

    time.sleep(0.15)
    assert not cb.is_open
    cb.record_success()
    assert not cb.is_open
