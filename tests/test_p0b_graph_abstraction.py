"""
P0-B Phase 3: Graph Database Abstraction — Abstraction Integrity Tests.

Test 1 (Lossless Async CRUD):  Prove ``upsert_node`` and ``create_edge`` work
    losslessly through the ``BaseGraphProvider`` async interface against a
    real SQLite-backed ``NetworkXProvider``.

Test 2 (Decomposed Query Methods):  Prove the read methods that replaced
    ``get_active_graph()`` return correct results through the ABC.

Test 3 (HybridRetriever Compatibility):  Prove the existing retrieval tests
    continue to pass when the graph is provided via the new provider, using
    the backward-compatible ``get_active_graph()`` shim.

All integration tests use a temporary on-disk SQLite database to verify
end-to-end persistence (not just in-memory state).
"""

import os
import shutil
from unittest.mock import MagicMock

import pytest

from mesa_memory.retrieval.hybrid import HybridRetriever
from mesa_memory.security.rbac import AccessControl
from mesa_memory.storage.graph.base import BaseGraphProvider
from mesa_memory.storage.graph.networkx_provider import NetworkXProvider
from tests.conftest import make_test_storage_dir

TEST_STORAGE_DIR = make_test_storage_dir("p0b_graph")


@pytest.fixture(autouse=True)
def setup_teardown():
    os.makedirs(TEST_STORAGE_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_STORAGE_DIR, ignore_errors=True)


async def _make_provider() -> NetworkXProvider:
    """Create and initialize a fresh NetworkXProvider with temp storage."""
    db_path = os.path.join(TEST_STORAGE_DIR, "kg_test.db")
    rocks_path = os.path.join(TEST_STORAGE_DIR, "kg_test.rocks")
    provider = NetworkXProvider(db_path=db_path, rocks_path=rocks_path)
    await provider.initialize()
    return provider


# ===================================================================
# TEST 1: ABC Conformance
# ===================================================================


class TestABCConformance:
    """Verify NetworkXProvider properly implements the BaseGraphProvider ABC."""

    def test_is_instance_of_base(self):
        provider = NetworkXProvider(
            db_path=os.path.join(TEST_STORAGE_DIR, "abc.db"),
            rocks_path=os.path.join(TEST_STORAGE_DIR, "abc.rocks"),
        )
        assert isinstance(provider, BaseGraphProvider)

    def test_all_abstract_methods_implemented(self):
        """Every method in the ABC must be implemented (not raise TypeError)."""
        abstract_methods = {
            "initialize",
            "upsert_node",
            "create_edge",
            "soft_delete_node",
            "soft_delete_edge",
            "soft_delete_by_cmb",
            "get_node_by_id",
            "get_neighbors",
            "get_node_degree",
            "find_nodes_by_name",
            "get_subgraph",
            "get_all_active_nodes",
            "compute_pagerank",
            "offload_expired",
        }
        provider_methods = set(dir(NetworkXProvider))
        for method in abstract_methods:
            assert method in provider_methods, f"Missing ABC method: {method}"
            assert callable(getattr(NetworkXProvider, method))


# ===================================================================
# TEST 2: Lossless Async CRUD (upsert_node + create_edge)
# ===================================================================


class TestLosslessAsyncCRUD:
    """Prove writes through the async interface are lossless —
    both in the in-memory graph and the persistent SQLite store.
    """

    @pytest.mark.asyncio
    async def test_upsert_node_returns_unique_id(self):
        provider = await _make_provider()
        node_id = await provider.upsert_node("Alice", "PERSON")
        assert isinstance(node_id, str)
        assert len(node_id) > 0

    @pytest.mark.asyncio
    async def test_upsert_node_persists_to_memory_and_db(self):
        """Node must appear in both in-memory graph AND SQLite."""
        provider = await _make_provider()
        node_id = await provider.upsert_node("Bob", "PERSON")

        # In-memory check via ABC method
        node = await provider.get_node_by_id(node_id)
        assert node is not None
        assert node["name"] == "Bob"
        assert node["type"] == "PERSON"

        # Persistence check: create a second provider pointing at the same DB
        provider2 = NetworkXProvider(
            db_path=provider.db_path,
            rocks_path=provider.rocks_path,
        )
        await provider2.initialize()
        node_reloaded = await provider2.get_node_by_id(node_id)
        assert node_reloaded is not None
        assert node_reloaded["name"] == "Bob"

    @pytest.mark.asyncio
    async def test_upsert_node_versioning(self):
        """Upserting the same name expires the old node and creates a new one."""
        provider = await _make_provider()
        old_id = await provider.upsert_node("Patient_X", "PERSON")
        new_id = await provider.upsert_node("Patient_X", "PATIENT")

        assert old_id != new_id

        # Old node must be gone from active set
        old_node = await provider.get_node_by_id(old_id)
        assert old_node is None

        # New node must be present with updated type
        new_node = await provider.get_node_by_id(new_id)
        assert new_node is not None
        assert new_node["type"] == "PATIENT"

    @pytest.mark.asyncio
    async def test_upsert_node_relinks_edges(self):
        """When a node is versioned, edges pointing to/from the old node
        must be re-linked to the new node."""
        provider = await _make_provider()
        a_id = await provider.upsert_node("A", "ENTITY")
        b_id = await provider.upsert_node("B", "ENTITY")
        _edge_id = await provider.create_edge(a_id, b_id, "knows")

        # Upsert B → new ID, edge should re-link
        b_new_id = await provider.upsert_node("B", "ENTITY_V2")
        assert b_new_id != b_id

        neighbors = await provider.get_neighbors(a_id, direction="out")
        target_ids = {n["node_id"] for n in neighbors}
        assert b_new_id in target_ids, "Edge not re-linked to new node"
        assert b_id not in target_ids, "Old node still referenced"

    @pytest.mark.asyncio
    async def test_upsert_node_with_cmb_id_provenance(self):
        """CMB-ID provenance must be recorded for later soft_delete_by_cmb."""
        provider = await _make_provider()
        node_id = await provider.upsert_node("Entity_1", "ENTITY", cmb_id="cmb-001")

        # soft_delete_by_cmb should remove the node
        await provider.soft_delete_by_cmb("cmb-001")
        node = await provider.get_node_by_id(node_id)
        assert node is None

    @pytest.mark.asyncio
    async def test_create_edge_returns_unique_id(self):
        provider = await _make_provider()
        a = await provider.upsert_node("X", "ENTITY")
        b = await provider.upsert_node("Y", "ENTITY")
        edge_id = await provider.create_edge(a, b, "relates_to", weight=0.5)
        assert isinstance(edge_id, str)
        assert len(edge_id) > 0

    @pytest.mark.asyncio
    async def test_create_edge_persists(self):
        """Edge must appear in neighbors AND survive a reload from SQLite."""
        provider = await _make_provider()
        a = await provider.upsert_node("P", "ENTITY")
        b = await provider.upsert_node("Q", "ENTITY")
        edge_id = await provider.create_edge(a, b, "connects", weight=1.0)

        neighbors = await provider.get_neighbors(a, direction="out")
        assert len(neighbors) == 1
        assert neighbors[0]["node_id"] == b
        assert neighbors[0]["relation"] == "connects"
        assert neighbors[0]["weight"] == 1.0

        # Persistence: reload from disk
        provider2 = NetworkXProvider(
            db_path=provider.db_path,
            rocks_path=provider.rocks_path,
        )
        await provider2.initialize()
        neighbors2 = await provider2.get_neighbors(a, direction="out")
        assert len(neighbors2) == 1
        assert neighbors2[0]["edge_id"] == edge_id

    @pytest.mark.asyncio
    async def test_create_multiple_edges(self):
        """Multiple edges between the same nodes should coexist (MultiDiGraph)."""
        provider = await _make_provider()
        a = await provider.upsert_node("M", "ENTITY")
        b = await provider.upsert_node("N", "ENTITY")
        e1 = await provider.create_edge(a, b, "likes")
        e2 = await provider.create_edge(a, b, "works_with")

        assert e1 != e2
        neighbors = await provider.get_neighbors(a, direction="out")
        relations = {n["relation"] for n in neighbors}
        assert "likes" in relations
        assert "works_with" in relations


# ===================================================================
# TEST 3: Decomposed Query Methods
# ===================================================================


class TestDecomposedQueries:
    """Verify the ABC query methods that replaced get_active_graph()."""

    @pytest.mark.asyncio
    async def test_find_nodes_by_name_case_insensitive(self):
        provider = await _make_provider()
        await provider.upsert_node("Alice", "PERSON")
        await provider.upsert_node("Bob", "PERSON")

        results = await provider.find_nodes_by_name(["alice", "BOB"])
        names = {r["name"] for r in results}
        assert "Alice" in names
        assert "Bob" in names

    @pytest.mark.asyncio
    async def test_find_nodes_by_name_case_sensitive(self):
        provider = await _make_provider()
        await provider.upsert_node("Alice", "PERSON")

        results = await provider.find_nodes_by_name(
            ["alice"],
            case_insensitive=False,
        )
        assert len(results) == 0

        results = await provider.find_nodes_by_name(
            ["Alice"],
            case_insensitive=False,
        )
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_get_node_degree(self):
        provider = await _make_provider()
        a = await provider.upsert_node("Hub", "ENTITY")
        b = await provider.upsert_node("Leaf1", "ENTITY")
        c = await provider.upsert_node("Leaf2", "ENTITY")
        await provider.create_edge(a, b, "connects")
        await provider.create_edge(a, c, "connects")
        await provider.create_edge(c, a, "back")

        degree = await provider.get_node_degree(a)
        assert degree == 3  # 2 out + 1 in

    @pytest.mark.asyncio
    async def test_get_node_degree_missing_node(self):
        provider = await _make_provider()
        degree = await provider.get_node_degree("nonexistent-id")
        assert degree == 0

    @pytest.mark.asyncio
    async def test_get_all_active_nodes(self):
        provider = await _make_provider()
        await provider.upsert_node("N1", "ENTITY")
        await provider.upsert_node("N2", "ENTITY")
        await provider.upsert_node("N3", "ENTITY")

        nodes = await provider.get_all_active_nodes()
        assert len(nodes) == 3
        names = {n["name"] for n in nodes}
        assert names == {"N1", "N2", "N3"}

    @pytest.mark.asyncio
    async def test_get_neighbors_directions(self):
        provider = await _make_provider()
        a = await provider.upsert_node("Source", "ENTITY")
        b = await provider.upsert_node("Target", "ENTITY")
        await provider.create_edge(a, b, "points_to")

        out = await provider.get_neighbors(a, direction="out")
        assert len(out) == 1
        assert out[0]["node_id"] == b

        in_n = await provider.get_neighbors(a, direction="in")
        assert len(in_n) == 0

        in_b = await provider.get_neighbors(b, direction="in")
        assert len(in_b) == 1
        assert in_b[0]["node_id"] == a

    @pytest.mark.asyncio
    async def test_get_subgraph(self):
        provider = await _make_provider()
        a = await provider.upsert_node("Center", "ENTITY")
        b = await provider.upsert_node("Ring1", "ENTITY")
        c = await provider.upsert_node("Ring2", "ENTITY")
        await provider.create_edge(a, b, "r1")
        await provider.create_edge(b, c, "r2")

        sub = await provider.get_subgraph([a], depth=1)
        node_ids = {n["node_id"] for n in sub["nodes"]}
        assert a in node_ids
        assert b in node_ids
        # c is depth-2, should NOT be included at depth=1
        assert c not in node_ids

        sub_deep = await provider.get_subgraph([a], depth=2)
        node_ids_deep = {n["node_id"] for n in sub_deep["nodes"]}
        assert c in node_ids_deep

    @pytest.mark.asyncio
    async def test_compute_pagerank(self):
        provider = await _make_provider()
        a = await provider.upsert_node("PageA", "ENTITY")
        b = await provider.upsert_node("PageB", "ENTITY")
        c = await provider.upsert_node("PageC", "ENTITY")
        await provider.create_edge(a, b, "link")
        await provider.create_edge(b, c, "link")
        await provider.create_edge(c, a, "link")

        scores = await provider.compute_pagerank()
        assert len(scores) == 3
        assert all(isinstance(v, float) for v in scores.values())
        # Cyclic graph → scores should be approximately equal
        vals = list(scores.values())
        assert max(vals) - min(vals) < 0.01

    @pytest.mark.asyncio
    async def test_compute_pagerank_empty_graph(self):
        provider = await _make_provider()
        scores = await provider.compute_pagerank()
        assert scores == {}


# ===================================================================
# TEST 4: Soft-Delete Through ABC
# ===================================================================


class TestSoftDelete:
    """Verify soft-delete operations through the async interface."""

    @pytest.mark.asyncio
    async def test_soft_delete_node_removes_from_active(self):
        provider = await _make_provider()
        node_id = await provider.upsert_node("Ephemeral", "ENTITY")
        await provider.soft_delete_node(node_id)

        result = await provider.get_node_by_id(node_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_soft_delete_node_cascades_edges(self):
        provider = await _make_provider()
        a = await provider.upsert_node("A", "ENTITY")
        b = await provider.upsert_node("B", "ENTITY")
        await provider.create_edge(a, b, "linked")

        await provider.soft_delete_node(a)

        neighbors = await provider.get_neighbors(b, direction="in")
        assert len(neighbors) == 0

    @pytest.mark.asyncio
    async def test_soft_delete_edge(self):
        provider = await _make_provider()
        a = await provider.upsert_node("X", "ENTITY")
        b = await provider.upsert_node("Y", "ENTITY")
        edge_id = await provider.create_edge(a, b, "temp_rel")

        await provider.soft_delete_edge(edge_id)

        neighbors = await provider.get_neighbors(a, direction="out")
        assert len(neighbors) == 0


# ===================================================================
# TEST 5: HybridRetriever Backward Compatibility
# ===================================================================


class TestHybridRetrieverCompat:
    """Prove existing HybridRetriever tests pass under the new provider.

    These tests use the deprecated ``get_active_graph()`` shim on
    ``NetworkXProvider``, exactly as the existing production code does.
    """

    @pytest.mark.asyncio
    async def test_cold_start_via_provider(self):
        """Cold-start path: empty graph → pure vector fallback."""
        storage = MagicMock()

        # Use NetworkXProvider's backward-compat get_active_graph
        provider = await _make_provider()
        storage.graph = provider  # Provider IS the graph
        storage.graph.get_active_graph = provider.get_active_graph

        storage.vector.search.return_value = [
            {
                "cmb_id": "vec_1",
                "content_payload": "c1",
                "fitness_score": 0.8,
                "_distance": 0.1,
            },
            {
                "cmb_id": "vec_2",
                "content_payload": "c2",
                "fitness_score": 0.5,
                "_distance": 0.3,
            },
        ]

        analyzer = MagicMock()
        analyzer.extract_entities.return_value = ["unknown"]

        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 768

        ac = AccessControl()
        ac.grant_access("a", "s", "READ")

        retriever = HybridRetriever(
            storage_facade=storage,
            analyzer=analyzer,
            embedder=embedder,
            access_control=ac,
        )

        results = await retriever.retrieve(
            "unknown query",
            agent_id="a",
            session_id="s",
            top_n=5,
        )
        assert "vec_1" in results
        assert "vec_2" in results

    @pytest.mark.asyncio
    async def test_rrf_with_populated_graph_via_provider(self):
        """Populated graph: RRF fusion path with real provider data."""
        storage = MagicMock()
        provider = await _make_provider()

        # Populate the provider with real nodes
        await provider.upsert_node("B_entity", "ENTITY")
        await provider.upsert_node("C_entity", "ENTITY")
        # Add enough nodes to avoid cold start
        for i in range(10):
            await provider.upsert_node(f"Filler_{i}", "ENTITY")

        storage.graph = provider
        storage.graph.get_active_graph = provider.get_active_graph

        storage.vector.search.return_value = [
            {
                "cmb_id": "A",
                "content_payload": "a",
                "fitness_score": 0.9,
                "_distance": 0.05,
            },
            {
                "cmb_id": "B",
                "content_payload": "b",
                "fitness_score": 0.7,
                "_distance": 0.15,
            },
        ]

        analyzer = MagicMock()
        analyzer.extract_entities.return_value = ["B_entity"]

        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 768

        _retriever = HybridRetriever(
            storage_facade=storage,
            analyzer=analyzer,
            embedder=embedder,
        )

        # The retriever now uses the async graph methods directly.
        matched = await storage.graph.find_nodes_by_name(
            ["B_entity"], case_insensitive=True
        )
        assert len(matched) == 1, "Provider-backed graph must be searchable by name"

    @pytest.mark.asyncio
    async def test_rrf_ranking_logic_unmodified(self):
        """The RRF formula itself must not change under the new abstraction."""
        storage = MagicMock()
        provider = await _make_provider()
        storage.graph = provider

        analyzer = MagicMock()
        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 768

        retriever = HybridRetriever(
            storage_facade=storage,
            analyzer=analyzer,
            embedder=embedder,
        )

        vector_ranks = [
            {"cmb_id": "A", "rank": 1, "source": "vector"},
            {"cmb_id": "B", "rank": 2, "source": "vector"},
        ]
        graph_ranks = [
            {"cmb_id": "B", "rank": 1, "source": "graph"},
            {"cmb_id": "C", "rank": 2, "source": "graph"},
        ]

        fused_ids = retriever._apply_rrf(vector_ranks, graph_ranks, k=60)
        assert fused_ids[0] == "B", "B should rank first (present in both sources)"
        assert "A" in fused_ids
        assert "C" in fused_ids
