"""Canonical Graph Projector for MESA V4.

Projects canonical FactCandidates / assertions into the derived Kùzu graph projection.

Architectural Invariants:
1. Graph is a derived projection, never the canonical source of truth.
2. Canonical memory is owned by SQLite (MemoryDAO / mutations).
3. GraphProjector never extracts facts from raw text.
4. Graph projection failures are logged/reported without destroying canonical SQL truth.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence
from uuid import NAMESPACE_URL, uuid5

from mesa_memory.extraction.service import FactCandidate

logger = logging.getLogger("MESA_GraphProjector")


class GraphProjectionError(RuntimeError):
    """Derived graph projection failure."""


class GraphProjector:
    """Projects canonical facts into the derived graph projection."""

    def __init__(self, dao: Any) -> None:
        self.dao = dao

    def _generate_deterministic_node_id(self, entity_name: str, agent_id: str) -> str:
        """Generate deterministic UUIDv5 node ID scoped to agent."""
        return str(uuid5(NAMESPACE_URL, f"{agent_id}:{entity_name.strip().lower()}"))

    async def project_facts(
        self,
        facts: Sequence[FactCandidate],
        *,
        agent_id: str,
        source_log_id: int | None = None,
        confidence_threshold: float = 0.5,
    ) -> dict[str, Any]:
        """Project canonical FactCandidates into the graph layer.

        Returns summary of projected nodes and edges.
        Does NOT throw to caller if graph insert fails, preserving canonical SQL truth.
        """
        projected_nodes: list[str] = []
        projected_edges: list[str] = []
        errors: list[str] = []

        for fact in facts:
            if fact.confidence is not None and fact.confidence < confidence_threshold:
                logger.debug(
                    "Skipping fact projection below confidence threshold: %s", fact
                )
                continue

            try:
                # 1. Upsert Subject Node
                subj_id = self._generate_deterministic_node_id(fact.subject, agent_id)
                await self._upsert_node(
                    node_id=subj_id,
                    name=fact.subject,
                    agent_id=agent_id,
                    metadata={"source_span": fact.source_span, "log_id": source_log_id},
                )
                projected_nodes.append(subj_id)

                # 2. Upsert Object Node
                obj_id = self._generate_deterministic_node_id(fact.object, agent_id)
                await self._upsert_node(
                    node_id=obj_id,
                    name=fact.object,
                    agent_id=agent_id,
                    metadata={"source_span": fact.source_span, "log_id": source_log_id},
                )
                projected_nodes.append(obj_id)

                # 3. Upsert Edge / Relation
                edge_weight = float(fact.confidence) if fact.confidence is not None else 1.0
                edge_id = await self._upsert_edge(
                    source_id=subj_id,
                    target_id=obj_id,
                    relation=fact.predicate,
                    weight=edge_weight,
                    agent_id=agent_id,
                    metadata={
                        "fact_text": fact.fact_text,
                        "valid_from": fact.valid_from,
                        "valid_to": fact.valid_to,
                        "supersedes": fact.supersedes,
                    },
                )
                if edge_id:
                    projected_edges.append(edge_id)

            except Exception as exc:
                err_msg = (
                    f"GRAPH_PROJECTION_WARNING | fact='{fact.fact_text}' "
                    f"agent_id={agent_id} error={exc}"
                )
                logger.warning(err_msg, exc_info=exc)
                errors.append(str(exc))
                # Preserves canonical SQL truth — does NOT re-raise to destroy memory mutation

        return {
            "success": len(errors) == 0,
            "projected_nodes": projected_nodes,
            "projected_edges": projected_edges,
            "errors": errors,
        }

    async def _upsert_node(
        self,
        node_id: str,
        name: str,
        agent_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        if hasattr(self.dao, "insert_memory"):
            return await self.dao.insert_memory(
                node_id=node_id,
                name=name,
                agent_id=agent_id,
                metadata=metadata or {},
            )
        return node_id

    async def _upsert_edge(
        self,
        source_id: str,
        target_id: str,
        relation: str,
        weight: float,
        agent_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        if hasattr(self.dao, "insert_edge"):
            return await self.dao.insert_edge(
                source_id=source_id,
                target_id=target_id,
                relation=relation,
                weight=weight,
                agent_id=agent_id,
                metadata=metadata or {},
            )
        return f"{source_id}->{relation}->{target_id}"
