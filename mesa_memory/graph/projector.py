"""Canonical Graph Projector for MESA V4.

Projects persisted canonical assertions into the derived Kùzu graph projection.

Architectural Invariants:
1. Graph is a derived projection, never the canonical source of truth.
2. Canonical memory is owned by SQLite (MemoryDAO / mutations).
3. GraphProjector never extracts facts from raw text.
4. Graph projection failures are logged/reported without destroying canonical SQL truth.
"""

from __future__ import annotations

from typing import Any, cast


class GraphProjectionError(RuntimeError):
    """Derived graph projection failure."""


class GraphProjector:
    """Production boundary for projecting durable canonical assertions to graph."""

    def __init__(self, dao: Any) -> None:
        self.dao = dao

    async def project_assertion(
        self, *, mutation: dict[str, Any], assertion: dict[str, Any]
    ) -> str:
        """Project one already-persisted assertion; never create canonical truth."""
        if not mutation.get("mutation_id"):
            raise GraphProjectionError("graph projection requires a canonical mutation")
        if not assertion.get("assertion_id") or not assertion.get("subject_id"):
            raise GraphProjectionError(
                "graph projection requires a persisted canonical assertion"
            )
        return cast(
            str,
            await self.dao.project_v4_graph_assertion(
                mutation=mutation,
                assertion=assertion,
            ),
        )
