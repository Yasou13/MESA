# MESA v0.6.0 — CrossEncoder Reranking Unit & Integration Tests
"""
Tests verifying the CrossEncoder reranking module, MemoryDAO batch fetching,
and HybridRetriever integration.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesa_memory.retrieval.hybrid import HybridRetriever
from mesa_memory.retrieval.reranker import CrossEncoderReranker
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema as init_kuzu_schema
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from tests.conftest import deterministic_embedding

TEST_DIR = os.path.join(os.path.dirname(__file__), ".test_storage_tmp", "crossencoder")


@pytest.fixture(autouse=True)
def _clean():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest.fixture
def dao_env():
    uid = uuid.uuid4().hex[:8]
    db = os.path.join(TEST_DIR, f"dao_{uid}.db")
    vec = os.path.join(TEST_DIR, f"vec_{uid}.lance")
    graph_path = os.path.join(TEST_DIR, f"graph_{uid}.kuzu")
    sql = AsyncEngine(db, max_connections=2)
    vec_eng = VectorEngine(vec, max_workers=1)
    init_kuzu_schema(graph_path)
    graph_eng = KuzuGraphProvider(db_path=graph_path)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(sql.initialize())
    loop.run_until_complete(initialize_schema(sql))
    loop.run_until_complete(vec_eng.initialize())
    loop.run_until_complete(graph_eng.initialize())
    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vec_eng, graph_provider=graph_eng)
    yield dao, sql, vec_eng, loop
    loop.run_until_complete(sql.close())
    loop.run_until_complete(vec_eng.close())
    loop.run_until_complete(graph_eng.close())
    loop.close()


@pytest.mark.asyncio
async def test_reranker_fallback_on_missing_model():
    """Verify that when sentence_transformers is missing or model fails, reranker falls back."""
    reranker = CrossEncoderReranker("nonexistent/model-test")
    # Simulate load failure / import error
    with patch(
        "builtins.__import__", side_effect=ImportError("mock no sentence_transformers")
    ):
        candidates = [
            {"cmb_id": "doc1", "content": "alpha content"},
            {"cmb_id": "doc2", "content": "beta content"},
        ]
        result = await reranker.rerank("query", candidates, top_k=2)
        assert result == ["doc1", "doc2"]
        assert reranker._load_failed is True


@pytest.mark.asyncio
async def test_reranker_predict_and_score():
    """Verify that valid prediction scores order candidates descending."""
    reranker = CrossEncoderReranker("test-model")
    mock_model = MagicMock()
    # Predict returns high score for doc2, low for doc1, mid for doc3
    mock_model.predict.return_value = [0.1, 0.9, 0.5]
    reranker._model = mock_model

    candidates = [
        {"cmb_id": "doc1", "content": "apple pie"},
        {"cmb_id": "doc2", "content": "quantum computing"},
        {"cmb_id": "doc3", "content": "machine learning"},
    ]
    result = await reranker.rerank("quantum physics", candidates, top_k=3)
    assert result == ["doc2", "doc3", "doc1"]
    mock_model.predict.assert_called_once()


def test_dao_get_nodes_by_ids_batch(dao_env):
    """Verify MemoryDAO.get_nodes_by_ids_batch fetches correctly with RLS enforcement."""
    dao, sql, vec_eng, loop = dao_env
    agent_id = "agent_alpha"
    other_agent = "agent_beta"
    vec8 = [0.1] * 8

    async def _run():
        await dao.insert_memory(
            agent_id=agent_id,
            node_id="n1",
            entity_name="Entity1",
            content="Content for node 1",
            embedding=vec8,
        )
        await dao.insert_memory(
            agent_id=agent_id,
            node_id="n2",
            entity_name="Entity2",
            content="Content for node 2",
            embedding=vec8,
        )
        await dao.insert_memory(
            agent_id=other_agent,
            node_id="n3",
            entity_name="Entity3",
            content="Other agent content",
            embedding=vec8,
        )

        # Batch fetch for agent_alpha
        res_alpha = await dao.get_nodes_by_ids_batch(
            agent_id, ["n1", "n2", "n3", "n_missing"]
        )
        assert len(res_alpha) == 2
        assert "n1" in res_alpha
        assert "n2" in res_alpha
        assert "n3" not in res_alpha
        assert res_alpha["n1"]["content_payload"] == "Content for node 1"

        # Batch fetch for agent_beta
        res_beta = await dao.get_nodes_by_ids_batch(other_agent, ["n1", "n2", "n3"])
        assert len(res_beta) == 1
        assert "n3" in res_beta

    loop.run_until_complete(_run())


@pytest.mark.asyncio
async def test_hybrid_retrieve_with_crossencoder():
    """Verify HybridRetriever integrates CrossEncoder and fetches batch content."""
    storage = MagicMock()
    storage.vector_engine = MagicMock()
    storage.vector_engine.compute_embedding = AsyncMock(return_value=[0.1] * 768)
    storage.get_memories = AsyncMock(return_value=[])
    storage.find_nodes_by_name = AsyncMock(return_value=[])
    storage.get_all_edges = AsyncMock(return_value=[])

    storage.search_memory = AsyncMock(
        return_value=[
            {"node_id": "c1", "content_hash": "hash1", "_distance": 0.1},
            {"node_id": "c2", "content_hash": "hash2", "_distance": 0.2},
            {"node_id": "c3", "content_hash": "hash3", "_distance": 0.3},
        ]
    )
    storage.get_epistemic_data_for_nodes = AsyncMock(return_value={})
    storage.get_nodes_by_ids_batch = AsyncMock(
        return_value={
            "c1": {"id": "c1", "content_payload": "text 1", "entity_name": "E1"},
            "c2": {"id": "c2", "content_payload": "text 2", "entity_name": "E2"},
            "c3": {"id": "c3", "content_payload": "text 3", "entity_name": "E3"},
        }
    )

    analyzer = MagicMock()
    analyzer.extract_entities.return_value = ["test_entity"]

    embedder = MagicMock()
    embedder.embed.side_effect = lambda text: deterministic_embedding(text, 768)

    from mesa_memory.security.rbac import AccessControl

    ac = AccessControl()
    await ac.initialize()
    await ac.grant_access("test_agent", "test_session", "READ")

    mock_reranker = MagicMock()
    # Reranker reverses order of c1, c2, c3
    mock_reranker.rerank = AsyncMock(return_value=["c3", "c2", "c1"][:2])

    retriever = HybridRetriever(
        dao=storage,
        analyzer=analyzer,
        embedder=embedder,
        access_control=ac,
        reranker=mock_reranker,
    )

    results = await retriever.retrieve(
        query_text="test query",
        agent_id="test_agent",
        session_id="test_session",
        top_n=2,
    )

    assert results == ["c3", "c2"]
    storage.get_nodes_by_ids_batch.assert_called_once()
    mock_reranker.rerank.assert_called_once()
