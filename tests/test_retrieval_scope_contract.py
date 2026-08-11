"""Shared vector retrieval scoping used by serving and rebuild verification."""

import sqlite3
from typing import Any

import pytest

from mesa_storage.retrieval_scope import (
    build_v4_lexical_query,
    scope_vector_result_ids,
)
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


def test_lexical_query_applies_dataset_ownership_before_ranking_limit() -> None:
    query = build_v4_lexical_query(dataset_count=2)

    assert "s.dataset_id IN (?,?)" in query
    assert "r.tenant_id = e.tenant_id" in query
    assert query.index("EXISTS") < query.index("ORDER BY rank")


def test_lexical_query_cannot_be_crowded_out_by_another_dataset() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE v4_entities (
            entity_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE v4_entities_fts USING fts5(
            canonical_name, entity_type,
            content='v4_entities', content_rowid='rowid'
        );
        CREATE TABLE artifact_registry (
            registry_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            physical_artifact_id TEXT NOT NULL,
            artifact_kind TEXT NOT NULL,
            state TEXT NOT NULL
        );
        CREATE TABLE artifact_sources (
            registry_id TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            mutation_id TEXT NOT NULL,
            state TEXT NOT NULL
        );
        CREATE TABLE memory_mutations (
            mutation_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL
        );
        INSERT INTO v4_entities VALUES
            ('denied', 'tenant-a', 'Court', 'concept', 'ACTIVE'),
            ('allowed', 'tenant-a', 'Court', 'concept', 'ACTIVE');
        INSERT INTO v4_entities_fts(rowid, canonical_name, entity_type)
            SELECT rowid, canonical_name, entity_type FROM v4_entities;
        INSERT INTO artifact_registry VALUES
            ('registry-denied', 'tenant-a', 'denied', 'ENTITY', 'ACTIVE'),
            ('registry-allowed', 'tenant-a', 'allowed', 'ENTITY', 'ACTIVE');
        INSERT INTO memory_mutations VALUES
            ('mutation-denied', 'agent-a'),
            ('mutation-allowed', 'agent-a');
        INSERT INTO artifact_sources VALUES
            ('registry-denied', 'dataset-b', 'mutation-denied', 'ACTIVE'),
            ('registry-allowed', 'dataset-a', 'mutation-allowed', 'ACTIVE');
        """)

    rows = connection.execute(
        build_v4_lexical_query(dataset_count=1),
        ('"Court"', "tenant-a", "agent-a", "dataset-a", 1),
    ).fetchall()
    connection.close()

    assert rows == [("allowed",)]


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
