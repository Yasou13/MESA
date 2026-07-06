"""
Graph Writer — Cross-validation and persistent graph commit logic.

All writes flow exclusively through the ``MemoryDAO``'s agent-scoped,
RLS-enforced methods.  ``MemoryDAO`` is the single source of truth.

``GraphWriter`` owns:

- Embedding pre-fetch (N+1 batching)
- Cross-validation scoring (composite similarity)
- Graph upsert (node + edge creation with weight tiers) via MemoryDAO
- Hub-node detection and human-review escalation
"""

import asyncio
import logging
from typing import Any

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
from mesa_memory.consolidation.lock import calculate_composite_similarity
from mesa_memory.consolidation.schemas import ExtractedTriplet
from mesa_storage.dao import MemoryDAO

logger = logging.getLogger("MESA_GraphWriter")

# Default agent_id for system-level consolidation operations.
# Production deployments should inject the owning agent_id from the
# batch records themselves.
_CONSOLIDATION_AGENT_ID = "mesa_consolidation_system"


class GraphWriter:
    """Commits cross-validated triplets to the knowledge graph via MemoryDAO.

    Responsibilities:
    1. Pre-fetch embeddings in batch to solve the N+1 problem.
    2. Score triplet pairs via composite similarity.
    3. Write to the graph at the appropriate weight tier via MemoryDAO.
    4. Detect hub-node divergences and escalate to human review.
    """

    def __init__(
        self,
        dao: MemoryDAO,
        embedder: BaseUniversalLLMAdapter,
        human_review_queue: Any,
        similarity_fn=None,
        agent_id: str = _CONSOLIDATION_AGENT_ID,
    ):
        self.dao = dao
        self.embedder = embedder
        self.human_review_queue = human_review_queue
        self._similarity_fn = similarity_fn or calculate_composite_similarity
        self._agent_id = agent_id

    # -------------------------------------------------------------------
    # Embedding pre-fetch (solves N+1)
    # -------------------------------------------------------------------

    async def prefetch_embeddings(
        self,
        sorted_batch: list[dict],
        indexed_a: dict[int, ExtractedTriplet],
        indexed_b: dict[int, ExtractedTriplet],
    ) -> dict[str, list[float]]:
        """Batch-fetch all unique entity/relation embeddings into a cache.

        Returns a dict mapping text → embedding vector.
        """
        unique_texts: set[str] = set()
        for idx in range(len(sorted_batch)):
            triplet_a = indexed_a.get(idx)
            triplet_b = indexed_b.get(idx)

            for t in self._triplet_texts(triplet_a) + self._triplet_texts(triplet_b):
                if t:
                    unique_texts.add(t)

        texts_list = list(unique_texts)
        embedding_cache: dict[str, list[float]] = {}
        if not texts_list:
            return embedding_cache

        # Prefer native async batch embedding
        try:
            embs = await self.embedder.aembed_batch(texts_list)
        except (NotImplementedError, AttributeError):
            # Fallback: individual async embeds via gather
            gather_results = await asyncio.gather(
                *(self.embedder.aembed(t) for t in texts_list),
                return_exceptions=True,
            )
            embs = []
            for i, emb in enumerate(gather_results):
                if isinstance(emb, BaseException):
                    logger.error(
                        "PREFETCH_EMBED_FAILED | text=%s error=%s",
                        texts_list[i][:50],
                        emb,
                        exc_info=emb,
                    )
                    embs.append([0.0] * getattr(self.embedder, "EMBEDDING_DIM", 384))
                else:
                    embs.append(emb)

        for t, e in zip(texts_list, embs):
            embedding_cache[t] = e

        return embedding_cache

    # -------------------------------------------------------------------
    # Cross-validate and commit
    # -------------------------------------------------------------------

    async def commit_batch(
        self,
        sorted_batch: list[dict],
        indexed_a: dict[int, ExtractedTriplet],
        indexed_b: dict[int, ExtractedTriplet],
        embedding_cache: dict[str, list[float]],
        batch_id: str,
        similarity_fn=None,
    ) -> tuple[int, int]:
        """Cross-validate triplet pairs and write to the knowledge graph.

        Args:
            similarity_fn: Optional override for the composite similarity
                function.  When ``None``, falls back to the instance default.

        Returns:
            (successful_writes, divergence_count)
        """
        _sim_fn = similarity_fn or self._similarity_fn
        successful_writes = 0
        divergence_count = 0

        for idx, record in enumerate(sorted_batch):
            cmb_id = record.get("cmb_id", "")
            agent_id = record.get("agent_id", self._agent_id)

            triplet_a = indexed_a.get(idx)
            triplet_b = indexed_b.get(idx)

            trip_a = self._to_dict(triplet_a)
            trip_b = self._to_dict(triplet_b)

            if not trip_a.get("head") or not trip_b.get("head"):
                # Nothing to extract — mark consolidated and skip
                await self._mark_record_consolidated(agent_id, cmb_id)
                continue

            sim_score = await _sim_fn(
                trip_a,
                trip_b,
                self.embedder,
                cache=embedding_cache,
            )

            if sim_score >= config.relation_similarity_threshold:
                await self._write_triplet(agent_id, cmb_id, trip_a, weight=1.0)
                successful_writes += 1

            elif (
                config.uncertain_zone_lower_bound
                <= sim_score
                < config.relation_similarity_threshold
            ):
                divergence_count += 1
                await self._write_triplet(agent_id, cmb_id, trip_a, weight=0.5)
                successful_writes += 1

            else:
                divergence_count += 1
                is_hub = await self._check_hub_node(
                    agent_id,
                    trip_a["head"],
                    trip_a["tail"],
                    trip_b["head"],
                    trip_b["tail"],
                )

                if is_hub:
                    await self.human_review_queue.aappend(
                        {
                            "batch_id": batch_id,
                            "cmb_id": cmb_id,
                            "triplet_a": trip_a,
                            "triplet_b": trip_b,
                            "sim_score": sim_score,
                        }
                    )
                    logger.warning(
                        "Record %s queued for human review "
                        "(hub node divergence, sim=%.4f)",
                        cmb_id,
                        sim_score,
                    )
                else:
                    logger.info(
                        "Record %s silently discarded " "(peripheral node, sim=%.4f)",
                        cmb_id,
                        sim_score,
                    )

            # Idempotency: mark ONLY after successful processing
            await self._mark_record_consolidated(agent_id, cmb_id)

        return successful_writes, divergence_count

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    async def _mark_record_consolidated(self, agent_id: str, cmb_id: str):
        """Mark a raw record as consolidated via MemoryDAO."""
        try:
            await self.dao.mark_consolidated(agent_id, node_id=cmb_id)
        except Exception as exc:
            logger.error(
                "MARK_CONSOLIDATED_FAILED | agent_id=%s cmb_id=%s error=%s",
                agent_id,
                cmb_id,
                exc,
            )

    async def _write_triplet(
        self, agent_id: str, cmb_id: str, triplet: dict, weight: float
    ):
        """Insert head/tail nodes and create an edge between them via MemoryDAO.

        Uses ``dao.insert_memory`` for graph vertex creation and
        ``dao.insert_edge`` for the relational link.  Both enforce
        agent_id RLS at the DAO boundary.
        """
        # Generate placeholder embeddings for the graph entities.
        # These are structural nodes — the real semantic vectors live in
        # the LanceDB hot-path records already persisted during ingestion.
        try:
            results = await asyncio.gather(
                self.embedder.aembed(triplet["head"]),
                self.embedder.aembed(triplet["tail"]),
                return_exceptions=True,
            )

            final_list = []
            for result in results:
                if isinstance(result, BaseException):
                    raise RuntimeError(f"Embedding failed: {result}")
                final_list.append(result)

            head_emb, tail_emb = final_list
        except Exception as exc:
            logger.warning(
                "EMBED_FAILED | cmb_id=%s error=%s — using zero vectors",
                cmb_id,
                exc,
            )
            dim = self.embedder.EMBEDDING_DIM
            head_emb = [0.0] * dim
            tail_emb = [0.0] * dim

        # Insert head entity node
        head_node_id = await self.dao.insert_memory(
            agent_id,
            entity_name=triplet["head"],
            content=f"[{cmb_id}] {triplet['head']}",
            embedding=head_emb,
            node_type="ENTITY",
        )

        # Insert tail entity node
        tail_node_id = await self.dao.insert_memory(
            agent_id,
            entity_name=triplet["tail"],
            content=f"[{cmb_id}] {triplet['tail']}",
            embedding=tail_emb,
            node_type="ENTITY",
        )

        # Link head → tail via the extracted relation
        await self.dao.insert_edge(
            agent_id,
            source_id=head_node_id,
            target_id=tail_node_id,
            relation_type=triplet["relation"],
            weight=weight,
        )

    async def _check_hub_node(self, agent_id: str, *entity_names: str) -> bool:
        """Check if any of the given entities are hub nodes (high degree)."""
        valid_names = [n for n in entity_names if n]
        if not valid_names:
            return False

        nodes = await self.dao.find_nodes_by_name(
            agent_id,
            names=valid_names,
            case_insensitive=True,
        )
        for node in nodes:
            degree = await self.dao.get_node_degree(
                agent_id,
                node_id=node["id"],
            )
            if degree >= config.hub_degree_threshold:
                return True
        return False

    @staticmethod
    def _to_dict(triplet: ExtractedTriplet | None) -> dict:
        """Convert an ExtractedTriplet to a plain dict, or empty sentinel."""
        if triplet:
            return {
                "head": triplet.head,
                "relation": triplet.relation,
                "tail": triplet.tail,
            }
        return {"head": "", "relation": "", "tail": ""}

    @staticmethod
    def _triplet_texts(triplet: ExtractedTriplet | None) -> list[str]:
        """Extract text fields from a triplet for embedding pre-fetch."""
        if triplet:
            return [triplet.head, triplet.relation, triplet.tail]
        return []
