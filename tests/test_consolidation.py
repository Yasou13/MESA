import json
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.observability.metrics import ObservabilityLayer
from tests.fixtures.vectors import VEC_MATCH


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

    await loop.run_batch()

    # When run_batch is called with no args, it queries the DAO
    dao.get_memories.assert_awaited()


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
