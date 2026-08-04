"""Shared vector retrieval scoping used by serving and rebuild verification."""

from typing import Any

import pytest

from mesa_storage.retrieval_scope import scope_vector_result_ids
from mesa_storage.vector_engine import VectorEngine


def test_vector_result_scope_excludes_cross_dataset_rows_and_preserves_rank() -> None:
    rows = [
        {"node_id": "dataset-b-nearest", "_distance": 0.01},
        {"node_id": "dataset-a-first", "_distance": 0.02},
        {"node_id": "dataset-a-second", "_distance": 0.03},
        {"node_id": "dataset-a-first", "_distance": 0.04},
    ]

    assert scope_vector_result_ids(
        rows, allowed_ids={"dataset-a-first", "dataset-a-second"}
    ) == ["dataset-a-first", "dataset-a-second"]


class _RecordingQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def metric(self, metric: str) -> "_RecordingQuery":
        self.calls.append(("metric", metric))
        return self

    def where(self, expression: str) -> "_RecordingQuery":
        self.calls.append(("where", expression))
        return self

    def limit(self, limit: int) -> "_RecordingQuery":
        self.calls.append(("limit", limit))
        return self

    def to_list(self) -> list[dict[str, Any]]:
        self.calls.append(("to_list", None))
        return [{"node_id": "allowed", "embedding": [1.0, 0.0]}]


class _RecordingTable:
    def __init__(self, query: _RecordingQuery) -> None:
        self.query = query

    def search(self, _query_vector: list[float]) -> _RecordingQuery:
        self.query.calls.append(("search", None))
        return self.query


@pytest.mark.asyncio
async def test_vector_candidates_are_filtered_before_ranking_limit() -> None:
    query = _RecordingQuery()
    engine = VectorEngine("unused")
    engine._initialized = True
    engine._tables["mesa_vectors_2"] = _RecordingTable(query)
    engine._list_table_names = lambda: ["mesa_vectors_2"]  # type: ignore[method-assign]

    try:
        results = await engine.search(
            [1.0, 0.0],
            agent_id="agent-a",
            allowed_node_ids={"allowed", "also-allowed"},
            limit=1,
        )
    finally:
        await engine.close()

    assert results == [{"node_id": "allowed"}]
    call_names = [name for name, _value in query.calls]
    assert call_names.index("where") < call_names.index("limit")
    where = dict(query.calls)["where"]
    assert "agent_id = 'agent-a'" in where
    assert "node_id IN ('allowed', 'also-allowed')" in where


@pytest.mark.asyncio
async def test_empty_vector_candidate_scope_fails_closed_without_searching() -> None:
    query = _RecordingQuery()
    engine = VectorEngine("unused")
    engine._initialized = True
    engine._tables["mesa_vectors_2"] = _RecordingTable(query)
    engine._list_table_names = lambda: ["mesa_vectors_2"]  # type: ignore[method-assign]

    try:
        assert await engine.search([1.0, 0.0], allowed_node_ids=set()) == []
    finally:
        await engine.close()

    assert query.calls == []
