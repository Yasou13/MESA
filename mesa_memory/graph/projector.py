"""Canonical Graph Projector for MESA V4.

Projects canonical FactCandidates / assertions into the derived Kùzu graph projection.

Architectural Invariants:
1. Graph is a derived projection, never the canonical source of truth.
2. Canonical memory is owned by SQLite (MemoryDAO / mutations).
3. GraphProjector never extracts facts from raw text.
4. Graph projection failures are logged/reported without destroying canonical SQL truth.
"""

from __future__ import annotations

from typing import Any


class GraphProjectionError(RuntimeError):
    """Derived graph projection failure."""


class GraphProjector:
    """Production boundary for projecting durable canonical assertions to graph."""

    def __init__(self, dao: Any) -> None:
        self.dao = dao

    async def project_triplet(
        self, *, mutation: dict[str, Any], triplet: dict[str, Any]
    ) -> str:
        """Project one already-extracted assertion; never parse or extract text."""
        if not mutation.get("mutation_id"):
            raise GraphProjectionError("graph projection requires a canonical mutation")
        if not triplet.get("head") or not triplet.get("relation"):
            raise GraphProjectionError(
                "graph projection requires a canonical assertion"
            )
        return await self.dao.project_v4_graph_triplet(
            mutation=mutation,
            triplet=triplet,
        )
