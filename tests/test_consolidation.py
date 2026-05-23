import json
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from mesa_memory.consolidation.lock import calculate_composite_similarity
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.observability.metrics import ObservabilityLayer


def _make_mock_embedder(dim=768):
    embedder = MagicMock()
    call_count = {"n": 0}

    def _embed(text, **kwargs):
        call_count["n"] += 1
        np.random.seed(hash(text.strip().lower()) % 2**31)
        return np.random.rand(dim).tolist()

    embedder.embed.side_effect = _embed
    embedder.EMBEDDING_DIM = dim
    return embedder


def _make_mock_dao():
    """Build a mock MemoryDAO with async methods pre-configured."""
    dao = MagicMock()
    dao.get_memories = AsyncMock(return_value=[])
    dao.insert_memory = AsyncMock(return_value="node_id")
    dao.insert_edge = AsyncMock(return_value="edge_id")
    dao.mark_consolidated = AsyncMock()
    dao.invalidate_node = AsyncMock()
    dao.find_nodes_by_name = AsyncMock(return_value=[])
    dao.get_node_degree = AsyncMock(return_value=0)
    dao.purge_memory = AsyncMock(return_value=0)
    dao.get_recent_telemetry_stats = AsyncMock(return_value={})
    return dao


def test_composite_similarity_alignment():
    embedder = MagicMock()

    vec_a = np.random.RandomState(1).rand(768).tolist()
    vec_b = np.random.RandomState(2).rand(768).tolist()
    vec_rel = np.random.RandomState(3).rand(768).tolist()

    def _embed(text, **kwargs):
        t = text.strip().lower()
        if t == "alice":
            return vec_a
        elif t == "bob":
            return vec_b
        elif t in ("likes", "is liked by"):
            return vec_rel
        return np.zeros(768).tolist()

    embedder.embed.side_effect = _embed

    trip_a = {"head": "Alice", "relation": "likes", "tail": "Bob"}
    trip_b = {"head": "Bob", "relation": "is liked by", "tail": "Alice"}

    score = calculate_composite_similarity(trip_a, trip_b, embedder)
    assert score >= 0.70


@pytest.mark.asyncio
async def test_consolidation_divergence_paths():
    obs = ObservabilityLayer()
    dao = _make_mock_dao()

    embedder = MagicMock()
    embedder.aembed = AsyncMock(return_value=[0.1] * 768)
    embedder.aembed_batch = AsyncMock(return_value=[[0.1] * 768])
    embedder.EMBEDDING_DIM = 768

    llm_a = MagicMock()
    llm_b = MagicMock()

    loop = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        llm_a=llm_a,
        llm_b=llm_b,
        obs_layer=obs,
    )

    record = {
        "cmb_id": "test-001",
        "content_payload": "test content",
        "source": "agent",
    }

    llm_a.complete.return_value = json.dumps(
        {"head": "X", "relation": "rel", "tail": "Y"}
    )
    llm_b.complete.return_value = json.dumps(
        {"head": "X", "relation": "rel", "tail": "Y"}
    )
    llm_a.acomplete = AsyncMock(
        return_value=json.dumps({"decision": "STORE", "justification": "test"})
    )
    llm_b.acomplete = AsyncMock(
        return_value=json.dumps({"decision": "STORE", "justification": "test"})
    )

    with patch(
        "mesa_memory.consolidation.loop.calculate_composite_similarity",
        return_value=0.4,
    ):
        await loop.run_batch([record])

    # Uncertain zone: edge should be inserted with weight 0.5
    assert dao.insert_edge.called
    call_kwargs = dao.insert_edge.call_args
    assert call_kwargs.kwargs.get("weight") == 0.5

    dao.reset_mock()
    loop.human_review_queue.clear()

    # Hub-node scenario: high degree → human review
    dao.find_nodes_by_name = AsyncMock(return_value=[{"id": "hub_1"}])
    dao.get_node_degree = AsyncMock(return_value=6)

    with patch(
        "mesa_memory.consolidation.loop.calculate_composite_similarity",
        return_value=0.2,
    ):
        await loop.run_batch([record])

    assert len(loop.human_review_queue) == 1
    assert not dao.insert_edge.called

    dao.reset_mock()
    loop.human_review_queue.clear()

    # Peripheral node: low degree → silent discard
    dao.find_nodes_by_name = AsyncMock(return_value=[{"id": "periph_1"}])
    dao.get_node_degree = AsyncMock(return_value=2)

    with patch(
        "mesa_memory.consolidation.loop.calculate_composite_similarity",
        return_value=0.2,
    ):
        await loop.run_batch([record])

    assert len(loop.human_review_queue) == 0
    assert not dao.insert_edge.called


@pytest.mark.asyncio
async def test_batch_processing_limit():
    obs = ObservabilityLayer()
    dao = _make_mock_dao()

    embedder = MagicMock()
    embedder.aembed = AsyncMock(return_value=[0.1] * 768)
    embedder.aembed_batch = AsyncMock(return_value=[[0.1] * 768])
    embedder.EMBEDDING_DIM = 768

    llm_a = MagicMock()
    llm_b = MagicMock()
    llm_a.complete.return_value = json.dumps(
        {"head": "A", "relation": "r", "tail": "B"}
    )
    llm_b.complete.return_value = json.dumps(
        {"head": "A", "relation": "r", "tail": "B"}
    )
    llm_a.acomplete = AsyncMock(
        return_value=json.dumps({"decision": "STORE", "justification": "test"})
    )
    llm_b.acomplete = AsyncMock(
        return_value=json.dumps({"decision": "STORE", "justification": "test"})
    )

    loop = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        llm_a=llm_a,
        llm_b=llm_b,
        obs_layer=obs,
    )

    with patch(
        "mesa_memory.consolidation.loop.calculate_composite_similarity",
        return_value=0.9,
    ):
        await loop.run_batch()

    # When run_batch is called with no args, it queries the DAO
    dao.get_memories.assert_awaited()


@pytest.mark.asyncio
async def test_rebel_extraction_fallback():
    obs = ObservabilityLayer()
    dao = _make_mock_dao()

    embedder = MagicMock()
    embedder.aembed = AsyncMock(return_value=[0.1] * 768)
    embedder.aembed_batch = AsyncMock(return_value=[[0.1] * 768])
    embedder.EMBEDDING_DIM = 768

    llm_a = MagicMock()
    llm_b = MagicMock()

    llm_a.complete.return_value = json.dumps(
        {"head": "Alice", "relation": "likes", "tail": "Bob"}
    )
    llm_b.complete.return_value = json.dumps(
        {"head": "Alice", "relation": "likes", "tail": "Bob"}
    )
    llm_a.acomplete = AsyncMock(
        return_value=json.dumps({"decision": "STORE", "justification": "test"})
    )
    llm_b.acomplete = AsyncMock(
        return_value=json.dumps({"decision": "STORE", "justification": "test"})
    )

    record = {"cmb_id": "r-1", "content_payload": "Alice likes Bob.", "source": "agent"}

    loop_obj = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        llm_a=llm_a,
        llm_b=llm_b,
        obs_layer=obs,
    )

    # Force Rebel Extractor to fail
    loop_obj.rebel_extractor.extract_triplets = MagicMock(return_value=[])

    with patch(
        "mesa_memory.consolidation.loop.calculate_composite_similarity",
        return_value=0.9,
    ):
        await loop_obj.run_batch([record])

    # Assert LLM WAS called because rebel failed
    assert llm_a.complete.called
    assert llm_b.complete.called


@pytest.mark.asyncio
async def test_rebel_extraction_success():
    obs = ObservabilityLayer()
    dao = _make_mock_dao()

    embedder = MagicMock()
    embedder.aembed = AsyncMock(return_value=[0.1] * 768)
    embedder.aembed_batch = AsyncMock(return_value=[[0.1] * 768])
    embedder.EMBEDDING_DIM = 768

    llm_a = MagicMock()
    llm_b = MagicMock()

    record = {"cmb_id": "r-2", "content_payload": "Alice likes Bob.", "source": "agent"}

    loop_obj = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        llm_a=llm_a,
        llm_b=llm_b,
        obs_layer=obs,
    )

    # Force Rebel Extractor to succeed
    loop_obj.rebel_extractor.extract_triplets = MagicMock(
        return_value=[{"head": "Alice", "relation": "likes", "tail": "Bob"}]
    )

    with patch(
        "mesa_memory.consolidation.loop.calculate_composite_similarity",
        return_value=0.9,
    ):
        await loop_obj.run_batch([record])

    # Assert LLM WAS NOT called because rebel succeeded
    assert not llm_a.complete.called
    assert not llm_b.complete.called
    assert dao.insert_edge.called


@pytest.mark.asyncio
async def test_tier3_discard_calls_invalidate_node():
    """Verify that the Tier-3 DISCARD path invalidates via DAO."""
    obs = ObservabilityLayer()
    dao = _make_mock_dao()

    embedder = MagicMock()
    embedder.aembed = AsyncMock(return_value=[0.1] * 768)
    embedder.EMBEDDING_DIM = 768

    llm_a = MagicMock()
    llm_b = MagicMock()

    # Both LLMs return DISCARD → unanimous discard
    llm_a.complete.return_value = json.dumps(
        {"decision": "DISCARD", "justification": "test"}
    )
    llm_b.complete.return_value = json.dumps(
        {"decision": "DISCARD", "justification": "test"}
    )
    llm_a.acomplete = AsyncMock(
        return_value=json.dumps({"decision": "DISCARD", "justification": "test"})
    )
    llm_b.acomplete = AsyncMock(
        return_value=json.dumps({"decision": "DISCARD", "justification": "test"})
    )

    loop_obj = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        llm_a=llm_a,
        llm_b=llm_b,
        obs_layer=obs,
    )

    deferred_record = {
        "cmb_id": "discard-001",
        "content_payload": "hallucinated data",
        "source": "agent",
        "tier3_deferred": True,
    }

    await loop_obj.run_batch([deferred_record])

    # invalidate_node on the DAO MUST be called
    dao.invalidate_node.assert_awaited_once()
    call_args = dao.invalidate_node.call_args
    assert call_args.kwargs["node_id"] == "discard-001"


@pytest.mark.asyncio
async def test_soft_delete_all_partial_failure():
    """Verify soft_delete_all raises RuntimeError on partial multi-store failure.

    This test validates the legacy StorageFacade's soft_delete_all method
    independently of the consolidation loop migration.
    """
    from mesa_memory.storage import StorageFacade

    facade = MagicMock(spec=StorageFacade)
    facade.raw_log = MagicMock()
    facade.raw_log.soft_delete = AsyncMock()
    facade.vector = MagicMock()
    facade.vector.soft_delete = MagicMock(side_effect=Exception("LanceDB offline"))
    facade.graph = MagicMock()
    facade.graph.soft_delete_by_cmb = AsyncMock()

    # Call the real implementation on the mock
    facade.soft_delete_all = StorageFacade.soft_delete_all.__get__(facade)

    with pytest.raises(RuntimeError, match="soft_delete_all failed at 'vector'"):
        await facade.soft_delete_all("fail-001")

    # raw_log succeeded before the failure
    facade.raw_log.soft_delete.assert_awaited_once_with("fail-001")
    # graph should NOT have been called since vector failed first
    facade.graph.soft_delete_by_cmb.assert_not_awaited()
