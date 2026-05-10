import json
import asyncio
import pytest
import pytest_asyncio
import numpy as np
from unittest.mock import MagicMock, AsyncMock, patch

from mesa_memory.consolidation.lock import calculate_composite_similarity
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.config import config
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

    storage = MagicMock()
    storage.raw_log = MagicMock()
    storage.raw_log.mark_consolidated = AsyncMock()
    storage.graph = MagicMock()
    storage.graph.upsert_node = AsyncMock(return_value="node_id")
    storage.graph.create_edge = AsyncMock(return_value="edge_id")

    embedder = MagicMock()
    llm_a = MagicMock()
    llm_b = MagicMock()

    loop = ConsolidationLoop(
        storage_facade=storage,
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

    llm_a.complete.return_value = json.dumps({"head": "X", "relation": "rel", "tail": "Y"})
    llm_b.complete.return_value = json.dumps({"head": "X", "relation": "rel", "tail": "Y"})

    with patch(
        "mesa_memory.consolidation.loop.calculate_composite_similarity",
        return_value=0.4,
    ):
        await loop.run_batch([record])

    assert storage.graph.create_edge.called
    call_kwargs = storage.graph.create_edge.call_args
    assert call_kwargs[1]["weight"] == 0.5 or call_kwargs.kwargs.get("weight") == 0.5

    storage.graph.reset_mock()
    storage.raw_log.reset_mock()
    loop.human_review_queue.clear()

    hub_graph = MagicMock()
    hub_node_data = [("hub_1", {"name": "X"})]
    hub_graph.nodes.return_value = hub_node_data
    hub_graph.degree.return_value = 6
    storage.graph.get_active_graph.return_value = hub_graph
    storage.graph.find_nodes_by_name = AsyncMock(return_value=[{"node_id": "hub_1"}])
    storage.graph.get_node_degree = AsyncMock(return_value=6)

    with patch(
        "mesa_memory.consolidation.loop.calculate_composite_similarity",
        return_value=0.2,
    ):
        await loop.run_batch([record])

    assert len(loop.human_review_queue) == 1
    assert not storage.graph.create_edge.called

    storage.graph.reset_mock()
    storage.raw_log.reset_mock()
    loop.human_review_queue.clear()

    periph_graph = MagicMock()
    periph_graph.nodes.return_value = [("periph_1", {"name": "X"})]
    periph_graph.degree.return_value = 2
    storage.graph.get_active_graph.return_value = periph_graph
    storage.graph.find_nodes_by_name = AsyncMock(return_value=[{"node_id": "periph_1"}])
    storage.graph.get_node_degree = AsyncMock(return_value=2)

    with patch(
        "mesa_memory.consolidation.loop.calculate_composite_similarity",
        return_value=0.2,
    ):
        await loop.run_batch([record])

    assert len(loop.human_review_queue) == 0
    assert not storage.graph.create_edge.called


@pytest.mark.asyncio
async def test_batch_processing_limit():
    obs = ObservabilityLayer()

    storage = MagicMock()
    storage.raw_log = MagicMock()

    all_records = [
        {"cmb_id": f"cmb-{i}", "content_payload": f"content {i}", "source": "agent"}
        for i in range(25)
    ]

    storage.raw_log.fetch_unconsolidated = AsyncMock(return_value=all_records[:config.consolidation_batch_size])
    storage.raw_log.mark_consolidated = AsyncMock()
    storage.graph = MagicMock()
    storage.graph.upsert_node = AsyncMock(return_value="node_id")
    storage.graph.create_edge = AsyncMock(return_value="edge_id")
    storage.graph.get_active_graph.return_value = MagicMock(
        nodes=MagicMock(return_value=[]),
    )

    llm_a = MagicMock()
    llm_b = MagicMock()
    llm_a.complete.return_value = json.dumps({"head": "A", "relation": "r", "tail": "B"})
    llm_b.complete.return_value = json.dumps({"head": "A", "relation": "r", "tail": "B"})

    embedder = MagicMock()

    loop = ConsolidationLoop(
        storage_facade=storage,
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

    assert storage.raw_log.fetch_unconsolidated.call_args[1]["limit"] == config.consolidation_batch_size
    assert storage.raw_log.mark_consolidated.call_count == config.consolidation_batch_size
