"""Deterministic graph-poisoning guardrail tests.

The release gate tests the audit's tenant-scoped Cypher result handling and
fatal threshold without requiring a live network reference dataset.
"""

from __future__ import annotations

import pytest

from mesa_evals import legal_audit


class _GraphRows:
    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = rows
        self.query: str | None = None
        self.parameters: dict[str, str] | None = None
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def execute_query(
        self, query: str, parameters: dict[str, str]
    ) -> list[list[str]]:
        self.query = query
        self.parameters = parameters
        return self.rows

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_graph_poisoning_audit_scopes_tenant_and_blocks_invalid_law(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _GraphRows(
        [
            ["Dava Konusu", "TBK Madde 49"],
            ["Dava Konusu", "Uydurma Kanun Madde 999"],
        ]
    )
    monkeypatch.setattr(legal_audit, "KuzuGraphProvider", lambda _: graph)

    result = await legal_audit.audit_graph(
        "/tmp/mesa-graph-audit.db", "tenant-a", threshold=0.05
    )

    assert graph.initialized and graph.closed
    assert graph.parameters == {"agent_id": "tenant-a"}
    assert "src.agent_id = $agent_id" in (graph.query or "")
    assert "tgt.agent_id = $agent_id" in (graph.query or "")
    assert "e.agent_id = $agent_id" in (graph.query or "")
    assert result.total_legal_edges == 2
    assert result.valid_edges == 1
    assert result.invalid_count == 1
    with pytest.raises(legal_audit.GraphPoisoningError):
        legal_audit.enforce_guardrail(result, threshold=0.05)


@pytest.mark.asyncio
async def test_graph_poisoning_audit_accepts_only_known_law_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _GraphRows([["Dava Konusu", "TBK m.49"]])
    monkeypatch.setattr(legal_audit, "KuzuGraphProvider", lambda _: graph)

    result = await legal_audit.audit_graph("/tmp/mesa-graph-audit.db", "tenant-a")

    assert result.valid_edges == 1
    assert result.invalid_count == 0
    legal_audit.enforce_guardrail(result)
