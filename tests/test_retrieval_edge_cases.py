"""
Retrieval Cold-Start Edge-Case Tests.

Verifies that the HybridRetriever gracefully handles:
1. Completely empty Knowledge Graph (no nodes, no edges).
2. Empty Vector Database (no embeddings stored).
3. Both stores empty simultaneously.
4. Query with no entity matches in the graph.
5. format_working_memory on empty results.
6. RBAC denial on retrieval.
"""

import os
import shutil
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory.observability.metrics import PROM_RETRIEVAL_DEGRADED
from mesa_memory.retrieval.core import QueryAnalyzer
from mesa_memory.retrieval.hybrid import HybridRetriever
from mesa_memory.security.rbac import AccessControl
from tests.conftest import deterministic_embedding, make_test_storage_dir

COLD_START_DIR = make_test_storage_dir("cold_start")


@pytest.fixture(autouse=True)
def cold_start_dir():
    os.makedirs(COLD_START_DIR, exist_ok=True)
    yield
    shutil.rmtree(COLD_START_DIR, ignore_errors=True)


def _make_mock_embedder(dim=128):
    """Create a mock embedder that returns deterministic, text-seeded vectors."""
    embedder = MagicMock()
    embedder.embed = MagicMock(
        side_effect=lambda text: deterministic_embedding(text, dim)
    )
    embedder.EMBEDDING_DIM = dim
    embedder.get_token_count = MagicMock(return_value=5)
    return embedder


def _make_mock_storage_facade(
    graph_nodes=None,
    vector_results=None,
):
    """Create a MemoryDAO mock with configurable responses."""
    storage = MagicMock()
    storage.vector_engine = MagicMock()
    storage.vector_engine.compute_embedding = AsyncMock(return_value=[0.1] * 768)

    # Graph mock
    storage.find_nodes_by_name = AsyncMock(return_value=graph_nodes or [])
    storage.get_memories = AsyncMock(return_value=graph_nodes or [])
    storage.get_all_edges = AsyncMock(return_value=[])

    # FTS Mock
    storage.search_memory_fts = AsyncMock(return_value=[])

    # Epistemic Mock
    storage.get_epistemic_data_for_nodes = AsyncMock(return_value={})

    # Vector mock
    storage.search_memory = AsyncMock(return_value=vector_results or [])

    return storage


async def _make_retriever(storage, ac=None, embedder=None):
    """Create a HybridRetriever with controlled dependencies."""
    if ac is None:
        ac = AccessControl(policy_path=os.path.join(COLD_START_DIR, "cold_rbac.db"))
        await ac.initialize()
        await ac.grant_access("test_agent", "test_session", "READ")
    analyzer = MagicMock(spec=QueryAnalyzer)
    analyzer.extract_entities = MagicMock(return_value=["test_entity"])
    return HybridRetriever(
        dao=storage,
        analyzer=analyzer,
        embedder=embedder or _make_mock_embedder(),
        access_control=ac,
    )


# --- Empty graph ---


class TestEmptyGraph:
    @pytest.mark.asyncio
    async def test_empty_graph_returns_empty_list(self):
        """Query on a graph with zero nodes → empty list, no crash."""
        storage = _make_mock_storage_facade(graph_nodes=[], vector_results=[])
        retriever = await _make_retriever(storage)

        result = await retriever.retrieve(
            "test query", "test_agent", "test_session", top_n=5
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_graph_no_entity_match(self):
        """Entities extracted but none found in graph → cold-start path."""
        storage = _make_mock_storage_facade(graph_nodes=[], vector_results=[])
        retriever = await _make_retriever(storage)

        result = await retriever.get_graph_results(
            "test_agent", ["CompanyX", "CompanyY"]
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_graph_spreading_on_empty_graph(self):
        """Graph spreading with no neighbors → empty result, no exception."""
        storage = _make_mock_storage_facade(graph_nodes=[{"id": "seed1"}])
        storage.graph_provider = AsyncMock()
        storage.graph_provider.get_cognitive_salience = AsyncMock(return_value=[])
        retriever = await _make_retriever(storage)

        result = await retriever.get_graph_results("test_agent", entities=["seed1"])
        assert result == []


# --- Empty vector store ---


class TestEmptyVectorStore:
    @pytest.mark.asyncio
    async def test_empty_vector_returns_empty(self):
        """Search on empty vector store → empty results."""
        storage = _make_mock_storage_facade(vector_results=[])
        retriever = await _make_retriever(storage)

        result = await retriever.get_vector_results("test_agent", "test query", k=10)
        assert result == []

    @pytest.mark.asyncio
    async def test_cold_start_rerank_empty(self):
        """Cold-start rerank on empty vector results → empty list."""
        storage = _make_mock_storage_facade()
        retriever = await _make_retriever(storage)

        result = retriever._cold_start_rerank([], top_k=5)
        assert result == []


# --- Both stores empty ---


class TestBothStoresEmpty:
    @pytest.mark.asyncio
    async def test_full_retrieve_both_empty(self):
        """Full retrieval pipeline with zero data → empty list, no crash."""
        storage = _make_mock_storage_facade(graph_nodes=[], vector_results=[])
        retriever = await _make_retriever(storage)

        result = await retriever.retrieve(
            "acquisition merger deal", "test_agent", "test_session"
        )
        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_retrieve_returns_list_type(self):
        """Return type is always list[str], even on empty stores."""
        storage = _make_mock_storage_facade()
        retriever = await _make_retriever(storage)

        result = await retriever.retrieve("anything", "test_agent", "test_session")
        assert isinstance(result, list)


# --- Cold-start with vector data only ---


class TestColdStartVectorOnly:
    @pytest.mark.asyncio
    async def test_cold_start_uses_vector_fallback(self):
        """With graph empty but vectors present → cold-start rerank path."""
        vector_data = [
            {
                "node_id": f"vec-{i}",
                "content_hash": f"Content {i}",
                "fitness_score": 0.5 + i * 0.05,
                "_distance": 0.1 * i,
            }
            for i in range(5)
        ]
        storage = _make_mock_storage_facade(graph_nodes=[], vector_results=vector_data)
        retriever = await _make_retriever(storage)

        result = await retriever.retrieve(
            "test query", "test_agent", "test_session", top_n=3
        )
        assert len(result) <= 3
        assert all(isinstance(r, str) for r in result)

    @pytest.mark.asyncio
    async def test_cold_start_rerank_ordering(self):
        """Cold-start rerank produces fitness+distance weighted ordering."""
        vector_results = [
            {"cmb_id": "low", "fitness_score": 0.1, "score": 0.1},
            {"cmb_id": "high", "fitness_score": 0.9, "score": 0.9},
            {"cmb_id": "mid", "fitness_score": 0.5, "score": 0.5},
        ]
        storage = _make_mock_storage_facade()
        retriever = await _make_retriever(storage)

        ranked = retriever._cold_start_rerank(vector_results, top_k=3)
        assert ranked[0]["cmb_id"] == "high"
        assert ranked[-1]["cmb_id"] == "low"

    @pytest.mark.asyncio
    async def test_cold_start_excludes_quarantined_vector_and_lexical_candidates(self):
        storage = _make_mock_storage_facade(
            graph_nodes=[],
            vector_results=[
                {"node_id": "quarantined", "_distance": 0.01},
                {"node_id": "allowed", "_distance": 0.02},
            ],
        )
        storage.search_memory_fts = AsyncMock(
            return_value=[{"id": "lexical-quarantined", "rank": -2.0}]
        )
        storage.get_epistemic_data_for_nodes = AsyncMock(
            return_value={
                "quarantined": {"is_quarantined": True},
                "lexical-quarantined": {"is_quarantined": True},
                "allowed": {"is_quarantined": False},
            }
        )
        retriever = await _make_retriever(storage)

        result = await retriever.retrieve(
            "test query", "test_agent", "test_session", top_n=5
        )

        assert result == ["allowed"]
        storage.get_epistemic_data_for_nodes.assert_awaited_once_with(
            "test_agent", ["quarantined", "allowed", "lexical-quarantined"]
        )

    @pytest.mark.asyncio
    async def test_cold_start_fails_closed_when_quarantine_lookup_fails(self):
        storage = _make_mock_storage_facade(
            graph_nodes=[], vector_results=[{"node_id": "candidate", "_distance": 0.1}]
        )
        storage.get_epistemic_data_for_nodes = AsyncMock(
            side_effect=RuntimeError("epistemic store unavailable")
        )
        retriever = await _make_retriever(storage)

        with pytest.raises(RuntimeError, match="epistemic store unavailable"):
            await retriever.retrieve("test query", "test_agent", "test_session")

    @pytest.mark.asyncio
    async def test_retrieval_reports_all_degraded_sources(self):
        storage = _make_mock_storage_facade()
        retriever = await _make_retriever(storage)
        retriever.get_vector_results = AsyncMock(side_effect=RuntimeError("vector"))
        retriever.get_graph_results = AsyncMock(side_effect=RuntimeError("graph"))
        storage.search_memory_fts = AsyncMock(side_effect=RuntimeError("lexical"))
        sources = ["graph", "lexical", "vector"]
        before = {
            source: PROM_RETRIEVAL_DEGRADED.labels(source=source)._value.get()
            for source in sources
        }

        result = await retriever.retrieve(
            "test query",
            "test_agent",
            "test_session",
            collect_diagnostics=True,
        )

        assert result["diagnostics"]["degraded_sources"] == sources
        for source in sources:
            assert PROM_RETRIEVAL_DEGRADED.labels(source=source)._value.get() == (
                before[source] + 1
            )


# --- format_working_memory edge cases ---


class TestFormatWorkingMemory:
    @pytest.mark.asyncio
    async def test_empty_nodes_returns_none_context(self):
        """Empty node list → 'Retrieved Context: None'."""
        storage = _make_mock_storage_facade()
        retriever = await _make_retriever(storage)

        result = retriever.format_working_memory([])
        assert result == "Retrieved Context: None"

    @pytest.mark.asyncio
    async def test_zero_token_budget(self):
        """Zero token budget → 'Retrieved Context: None'."""
        embedder = _make_mock_embedder()
        embedder.get_token_count = MagicMock(return_value=100)
        storage = _make_mock_storage_facade()
        retriever = await _make_retriever(storage, embedder=embedder)

        result = retriever.format_working_memory(
            [{"content_payload": "test"}], max_tokens=5
        )
        assert result == "Retrieved Context: None"

    @pytest.mark.asyncio
    async def test_single_node_fits(self):
        """Single node within budget → formatted context string."""
        embedder = _make_mock_embedder()
        embedder.get_token_count = MagicMock(return_value=3)
        storage = _make_mock_storage_facade()
        retriever = await _make_retriever(storage, embedder=embedder)

        result = retriever.format_working_memory(
            [{"content_payload": "Acme acquired Globex", "source": "test"}],
            max_tokens=100,
        )
        assert "Acme acquired Globex" in result
        assert result.startswith("Retrieved Context:")


# --- RBAC enforcement on retrieval ---


class TestRetrievalRBACEnforcement:
    @pytest.mark.asyncio
    async def test_no_access_raises_permission_error(self):
        """Agent without READ access → PermissionError."""
        ac = AccessControl(policy_path=os.path.join(COLD_START_DIR, "rbac_deny.db"))
        await ac.initialize()
        # Deliberately NOT granting access
        storage = _make_mock_storage_facade()
        retriever = await _make_retriever(storage, ac=ac)

        with pytest.raises(PermissionError, match="lacks READ access"):
            await retriever.retrieve("test", "unauthorized_agent", "some_session")

    @pytest.mark.asyncio
    async def test_revoked_access_denied(self):
        """Access revoked mid-session → immediate denial."""
        ac = AccessControl(policy_path=os.path.join(COLD_START_DIR, "rbac_revoke.db"))
        await ac.initialize()
        await ac.grant_access("temp_agent", "temp_session", "READ")
        storage = _make_mock_storage_facade()
        retriever = await _make_retriever(storage, ac=ac)

        # First call succeeds
        result = await retriever.retrieve("test", "temp_agent", "temp_session")
        assert isinstance(result, list)

        # Revoke and re-try
        await ac.revoke_access("temp_agent", "temp_session")
        with pytest.raises(PermissionError):
            await retriever.retrieve("test", "temp_agent", "temp_session")


# --- Alpha-Reranking with empty inputs ---


class TestAlphaRerankingEdgeCases:
    @pytest.mark.asyncio
    async def test_alpha_reranking_both_empty(self):
        """Alpha-Reranking with empty vector and graph results → empty list."""
        storage = _make_mock_storage_facade()
        retriever = await _make_retriever(storage)

        result = await retriever._apply_alpha_reranking("test_agent", [], [], [])
        assert result == []

    @pytest.mark.asyncio
    async def test_alpha_reranking_vector_only(self):
        """Alpha-Reranking with vector results but no graph → ordered by vector rank."""
        storage = _make_mock_storage_facade()
        retriever = await _make_retriever(storage)

        vector = [
            {"cmb_id": "v1", "score": 0.9},
            {"cmb_id": "v2", "score": 0.5},
        ]
        result = await retriever._apply_alpha_reranking("test_agent", vector, [], [])
        assert result[0] == "v1"
        assert result[1] == "v2"

    @pytest.mark.asyncio
    async def test_alpha_reranking_graph_only(self):
        """Alpha-Reranking with graph results but no vector → ordered by graph rank."""
        storage = _make_mock_storage_facade()
        retriever = await _make_retriever(storage)

        graph = [
            {"cmb_id": "g1", "score": 0.5},
            {"cmb_id": "g2", "score": 0.01},
        ]

        result = await retriever._apply_rrf_reranking("test_agent", [], graph, [])
        assert result[0] == "g1"
        assert result[1] == "g2"
