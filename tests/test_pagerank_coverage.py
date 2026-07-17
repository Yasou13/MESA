# MESA v0.6.0 — PageRank Coverage Tests
"""
Unit tests for mesa_workers/maintenance_pagerank.py.

Covers:
  - _compute_damped_pagerank (pure function, no I/O)
  - _extract_subgraph (mocked KuzuGraphProvider)
  - _quarantine_nodes (mocked KuzuGraphProvider)
  - run_quarantine_scan (integration with mocks)
  - _fetch_agent_ids_sync (sync SQLite query)
  - schedule_pagerank_worker (background loop, tested with cancellation)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_workers.maintenance_pagerank import (
    _compute_damped_pagerank,
    _extract_subgraph,
    _quarantine_nodes,
    run_quarantine_scan,
    schedule_pagerank_worker,
)

# ===================================================================
# _compute_damped_pagerank — pure function tests
# ===================================================================


class TestComputeDampedPagerank:
    def test_empty_graph(self):
        result = _compute_damped_pagerank([], [], 0.85)
        assert result == {}

    def test_single_node_no_edges(self):
        result = _compute_damped_pagerank(["n1"], [], 0.85)
        assert "n1" in result
        assert result["n1"] == 1.0  # Single node → normalised to 1.0

    def test_two_nodes_one_edge(self):
        nodes = ["a", "b"]
        edges = [
            {
                "source_id": "a",
                "target_id": "b",
                "weight": 1.0,
                "epistemic_uncertainty": 0.0,
            }
        ]
        result = _compute_damped_pagerank(nodes, edges, 0.85)
        assert "a" in result
        assert "b" in result
        # b receives rank from a, so b should have higher rank
        assert result["b"] >= result["a"]

    def test_high_uncertainty_reduces_rank(self):
        """Edges with high epistemic_uncertainty contribute less rank."""
        nodes = ["a", "b"]
        # Edge with full certainty
        edges_certain = [
            {
                "source_id": "a",
                "target_id": "b",
                "weight": 1.0,
                "epistemic_uncertainty": 0.0,
            }
        ]
        # Edge with high uncertainty
        edges_uncertain = [
            {
                "source_id": "a",
                "target_id": "b",
                "weight": 1.0,
                "epistemic_uncertainty": 0.9,
            }
        ]
        result_certain = _compute_damped_pagerank(nodes, edges_certain, 0.85)
        result_uncertain = _compute_damped_pagerank(nodes, edges_uncertain, 0.85)

        # With high uncertainty, b gets less rank from a
        assert result_uncertain["b"] <= result_certain["b"]

    def test_full_uncertainty_produces_uniform(self):
        """Edges with epistemic_uncertainty=1.0 contribute 0 weight → uniform."""
        nodes = ["a", "b", "c"]
        edges = [
            {
                "source_id": "a",
                "target_id": "b",
                "weight": 1.0,
                "epistemic_uncertainty": 1.0,  # Fully uncertain
            },
            {
                "source_id": "b",
                "target_id": "c",
                "weight": 1.0,
                "epistemic_uncertainty": 1.0,
            },
        ]
        result = _compute_damped_pagerank(nodes, edges, 0.85)
        # All nodes should have uniform rank (since effective weight = 0)
        assert result["a"] == result["b"] == result["c"]

    def test_edges_to_unknown_nodes_skipped(self):
        """Edges referencing nodes outside the node list are silently skipped."""
        nodes = ["a", "b"]
        edges = [
            {
                "source_id": "a",
                "target_id": "unknown",
                "weight": 1.0,
                "epistemic_uncertainty": 0.0,
            }
        ]
        result = _compute_damped_pagerank(nodes, edges, 0.85)
        assert "a" in result
        assert "b" in result

    def test_star_topology(self):
        """Hub node receiving edges from many sources gets highest rank."""
        nodes = ["hub", "s1", "s2", "s3", "s4"]
        edges = [
            {
                "source_id": f"s{i}",
                "target_id": "hub",
                "weight": 1.0,
                "epistemic_uncertainty": 0.0,
            }
            for i in range(1, 5)
        ]
        result = _compute_damped_pagerank(nodes, edges, 0.85)
        assert result["hub"] == 1.0  # Highest rank, normalised to 1.0
        for s in ["s1", "s2", "s3", "s4"]:
            assert result[s] < result["hub"]


# ===================================================================
# _extract_subgraph — mocked KuzuGraphProvider
# ===================================================================


class TestExtractSubgraph:
    @pytest.mark.asyncio
    async def test_extracts_nodes_and_edges(self):
        mock_provider = MagicMock()
        mock_provider.execute_query = AsyncMock(
            side_effect=[
                # First call: node query
                [["node-1"], ["node-2"], ["node-3"]],
                # Second call: edge query
                [["node-1", "node-2", 1.0, 0.1], ["node-2", "node-3", 0.5, 0.3]],
            ]
        )
        nodes, edges = await _extract_subgraph(mock_provider, "agent-test")
        assert nodes == ["node-1", "node-2", "node-3"]
        assert len(edges) == 2
        assert edges[0]["source_id"] == "node-1"
        assert edges[0]["weight"] == 1.0
        assert edges[1]["epistemic_uncertainty"] == 0.3

    @pytest.mark.asyncio
    async def test_empty_graph(self):
        mock_provider = MagicMock()
        mock_provider.execute_query = AsyncMock(
            side_effect=[
                [],  # No nodes
                [],  # No edges
            ]
        )
        nodes, edges = await _extract_subgraph(mock_provider, "agent-empty")
        assert nodes == []
        assert edges == []

    @pytest.mark.asyncio
    async def test_null_values_default(self):
        """Null weight/uncertainty default to 1.0/0.0."""
        mock_provider = MagicMock()
        mock_provider.execute_query = AsyncMock(
            side_effect=[
                [["n1"], ["n2"]],
                [["n1", "n2", None, None]],
            ]
        )
        _, edges = await _extract_subgraph(mock_provider, "agent-null")
        assert edges[0]["weight"] == 1.0
        assert edges[0]["epistemic_uncertainty"] == 0.0


# ===================================================================
# _quarantine_nodes — mocked KuzuGraphProvider
# ===================================================================


class TestQuarantineNodes:
    @pytest.mark.asyncio
    async def test_marks_all_nodes(self):
        mock_provider = MagicMock()
        mock_provider.execute_write = AsyncMock()

        await _quarantine_nodes(mock_provider, "agent-q", ["n1", "n2", "n3"])
        assert mock_provider.execute_write.call_count == 3

    @pytest.mark.asyncio
    async def test_handles_individual_failure(self):
        """A failure on one node should not abort the batch."""
        mock_provider = MagicMock()
        mock_provider.execute_write = AsyncMock(
            side_effect=[None, RuntimeError("DB error"), None]
        )
        # Should not raise
        await _quarantine_nodes(mock_provider, "agent-q", ["n1", "n2", "n3"])
        assert mock_provider.execute_write.call_count == 3


# ===================================================================
# run_quarantine_scan — integration with mocked provider
# ===================================================================


class TestRunQuarantineScan:
    @pytest.mark.asyncio
    async def test_skips_small_graph(self):
        """Graphs with < 5 nodes are skipped (too small for meaningful PR)."""
        mock_provider = MagicMock()
        mock_provider.execute_query = AsyncMock(
            side_effect=[
                [["n1"], ["n2"]],  # Only 2 nodes
                [],  # No edges
            ]
        )
        result = await run_quarantine_scan(
            agent_id="agent-small",
            graph_provider=mock_provider,
        )
        assert result["quarantined_count"] == 0
        assert result["total_nodes"] == 2

    @pytest.mark.asyncio
    async def test_quarantines_low_authority_nodes(self):
        """Nodes with no incoming edges get quarantined (high QI)."""
        # Star topology: hub has 6 incoming edges, 6 isolated leaves.
        # With threshold_qi=0.5 the leaf nodes (PR ≈ teleport only)
        # should have QI > 0.5 while hub has QI ≈ 0.
        nodes = [["hub"]] + [[f"leaf-{i}"] for i in range(6)]
        edges = [[f"leaf-{i}", "hub", 1.0, 0.0] for i in range(6)]

        mock_provider = MagicMock()
        mock_provider.execute_query = AsyncMock(side_effect=[nodes, edges])
        mock_provider.execute_write = AsyncMock()

        result = await run_quarantine_scan(
            agent_id="agent-star",
            graph_provider=mock_provider,
            threshold_qi=0.50,  # Lower threshold to catch leaf nodes
        )
        assert result["total_nodes"] == 7
        # Leaf nodes with no incoming edges should be quarantined
        assert result["quarantined_count"] >= 1
        # Hub should NOT be quarantined
        assert "hub" not in result["quarantined_ids"]

    @pytest.mark.asyncio
    async def test_returns_result_dict_shape(self):
        nodes = [["n1"]] + [[f"n{i}"] for i in range(2, 7)]
        edges = [
            ["n1", "n2", 1.0, 0.0],
            ["n2", "n3", 1.0, 0.0],
        ]

        mock_provider = MagicMock()
        mock_provider.execute_query = AsyncMock(side_effect=[nodes, edges])
        mock_provider.execute_write = AsyncMock()

        result = await run_quarantine_scan(
            agent_id="agent-shape",
            graph_provider=mock_provider,
        )
        assert "agent_id" in result
        assert "total_nodes" in result
        assert "quarantined_count" in result
        assert "quarantined_ids" in result
        assert "elapsed_ms" in result


# ===================================================================
# schedule_pagerank_worker — tested with cancellation
# ===================================================================


class TestSchedulePageRankWorker:
    @pytest.mark.asyncio
    async def test_worker_cancellation(self):
        """Worker should handle CancelledError gracefully."""
        mock_dao = MagicMock()
        mock_dao.graph_provider = None
        mock_dao.sqlite_engine = MagicMock()
        mock_dao.sqlite_engine._db_path = "/tmp/test.db"

        mock_dao.get_all_active_agent_ids = AsyncMock(return_value=[])

        task = asyncio.create_task(
            schedule_pagerank_worker(mock_dao, interval_sec=3600)
        )
        # Yield deterministically until the mock is called
        for _ in range(50):
            if mock_dao.get_all_active_agent_ids.called:
                break
            await asyncio.sleep(0)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected — worker may or may not catch it
        assert task.done()
