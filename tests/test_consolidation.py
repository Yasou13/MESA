import json
from tests.fixtures.vectors import VEC_MATCH, VEC_NEAR, VEC_BASE
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
    embedder.aembed = AsyncMock(side_effect=_embed)
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


@pytest.mark.asyncio
async def test_composite_similarity_alignment():
    embedder = MagicMock()

    def _embed(text, **kwargs):
        t = text.strip().lower()
        if t == "match":
            return VEC_MATCH
        elif t == "near":
            return VEC_NEAR
        return VEC_BASE

    embedder.embed.side_effect = _embed
    embedder.aembed = AsyncMock(side_effect=_embed)

    trip_base = {"head": "base", "relation": "base", "tail": "base"}
    trip_match = {"head": "match", "relation": "match", "tail": "match"}
    trip_near = {"head": "near", "relation": "near", "tail": "near"}

    # VEC_MATCH cos_sim(VEC_BASE, VEC_MATCH) = 0.95 (>= 0.80 merge threshold)
    score_match = await calculate_composite_similarity(trip_base, trip_match, embedder)
    assert score_match >= 0.80

    # VEC_NEAR cos_sim(VEC_BASE, VEC_NEAR) = 0.79 (< 0.80 merge threshold)
    score_near = await calculate_composite_similarity(trip_base, trip_near, embedder)
    assert score_near < 0.80


@pytest.mark.asyncio
async def test_consolidation_divergence_paths():
    obs = ObservabilityLayer()
    dao = _make_mock_dao()

    embedder = MagicMock()
    embedder.aembed = AsyncMock(return_value=VEC_MATCH)
    embedder.aembed_batch = AsyncMock(return_value=[VEC_MATCH])
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
    await loop.human_review_queue.clear()

    # Hub-node scenario: high degree → human review
    dao.find_nodes_by_name = AsyncMock(return_value=[{"id": "hub_1"}])
    dao.get_node_degree = AsyncMock(return_value=6)

    with patch(
        "mesa_memory.consolidation.loop.calculate_composite_similarity",
        return_value=0.2,
    ):
        await loop.run_batch([record])

    assert await loop.human_review_queue.alen() == 1
    assert not dao.insert_edge.called

    dao.reset_mock()
    await loop.human_review_queue.clear()

    # Peripheral node: low degree → silent discard
    dao.find_nodes_by_name = AsyncMock(return_value=[{"id": "periph_1"}])
    dao.get_node_degree = AsyncMock(return_value=2)

    with patch(
        "mesa_memory.consolidation.loop.calculate_composite_similarity",
        return_value=0.2,
    ):
        await loop.run_batch([record])

    assert await loop.human_review_queue.alen() == 0
    assert not dao.insert_edge.called


@pytest.mark.asyncio
async def test_batch_processing_limit():
    obs = ObservabilityLayer()
    dao = _make_mock_dao()

    embedder = MagicMock()
    embedder.aembed = AsyncMock(return_value=VEC_MATCH)
    embedder.aembed_batch = AsyncMock(return_value=[VEC_MATCH])
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
    embedder.aembed = AsyncMock(return_value=VEC_MATCH)
    embedder.aembed_batch = AsyncMock(return_value=[VEC_MATCH])
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
    embedder.aembed = AsyncMock(return_value=VEC_MATCH)
    embedder.aembed_batch = AsyncMock(return_value=[VEC_MATCH])
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
    embedder.aembed = AsyncMock(return_value=VEC_MATCH)
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
