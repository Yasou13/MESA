"""Tests for Task F013: Canonical Graph Projector."""

from unittest.mock import AsyncMock, MagicMock
import pytest

from mesa_memory.extraction.service import FactCandidate
from mesa_memory.graph.projector import GraphProjector


@pytest.mark.asyncio
async def test_graph_projector_projects_canonical_facts():
    dao = MagicMock()
    dao.insert_memory = AsyncMock(side_effect=lambda node_id, **kwargs: node_id)
    dao.insert_edge = AsyncMock(return_value="edge_123")

    projector = GraphProjector(dao=dao)

    facts = [
        FactCandidate(
            fact_text="Alice works at Acme Corp",
            subject="Alice",
            predicate="WORKS_AT",
            object="Acme Corp",
            confidence=0.95,
            source_span="Alice works at Acme Corp",
        ),
        FactCandidate(
            fact_text="Acme Corp located in Istanbul",
            subject="Acme Corp",
            predicate="LOCATED_IN",
            object="Istanbul",
            confidence=0.90,
        ),
    ]

    result = await projector.project_facts(facts, agent_id="agent_1")

    assert result["success"] is True
    assert len(result["projected_nodes"]) == 4  # 2 facts * (subj, obj)
    assert len(result["projected_edges"]) == 2
    assert dao.insert_memory.call_count == 4
    assert dao.insert_edge.call_count == 2


@pytest.mark.asyncio
async def test_graph_projector_filters_low_confidence_facts():
    dao = MagicMock()
    dao.insert_memory = AsyncMock(return_value="node_1")
    dao.insert_edge = AsyncMock(return_value="edge_1")

    projector = GraphProjector(dao=dao)

    facts = [
        FactCandidate(
            fact_text="Uncertain rumour",
            subject="Bob",
            predicate="MAY_BE",
            object="Somewhere",
            confidence=0.2,  # Below 0.5 threshold
        )
    ]

    result = await projector.project_facts(facts, agent_id="agent_1", confidence_threshold=0.5)

    assert result["success"] is True
    assert len(result["projected_nodes"]) == 0
    assert len(result["projected_edges"]) == 0
    assert dao.insert_memory.call_count == 0


@pytest.mark.asyncio
async def test_graph_failure_does_not_raise_to_preserve_canonical_truth():
    dao = MagicMock()
    dao.insert_memory = AsyncMock(side_effect=RuntimeError("Kùzu disk lock error"))

    projector = GraphProjector(dao=dao)

    facts = [
        FactCandidate(
            fact_text="Critical canonical fact",
            subject="Server1",
            predicate="RUNS",
            object="PostgreSQL",
            confidence=1.0,
        )
    ]

    # Should not raise exception
    result = await projector.project_facts(facts, agent_id="agent_1")

    assert result["success"] is False
    assert len(result["errors"]) == 1
    assert "Kùzu disk lock error" in result["errors"][0]
