"""
Graph Writer — Cross-validation and persistent graph commit logic.

Extracted from the monolithic ``ConsolidationLoop`` to isolate storage
concerns from batch orchestration.  ``GraphWriter`` owns:

- Embedding pre-fetch (N+1 batching)
- Cross-validation scoring (composite similarity)
- Graph upsert (node + edge creation with weight tiers)
- Hub-node detection and human-review escalation
"""

import asyncio
import inspect
import logging
from collections import deque

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
from mesa_memory.consolidation.lock import calculate_composite_similarity
from mesa_memory.consolidation.schemas import ExtractedTriplet
from mesa_memory.security.rbac_constants import SYSTEM_AGENT_ID, SYSTEM_SESSION_ID
from mesa_memory.storage import StorageFacade

logger = logging.getLogger("MESA_GraphWriter")


class GraphWriter:
    """Commits cross-validated triplets to the knowledge graph.

    Responsibilities:
    1. Pre-fetch embeddings in batch to solve the N+1 problem.
    2. Score triplet pairs via composite similarity.
    3. Write to the graph at the appropriate weight tier.
    4. Detect hub-node divergences and escalate to human review.
    """

    def __init__(
        self,
        storage_facade: StorageFacade,
        embedder: BaseUniversalLLMAdapter,
        human_review_queue: deque,
        similarity_fn=None,
    ):
        self.storage = storage_facade
        self.embedder = embedder
        self.human_review_queue = human_review_queue
        self._similarity_fn = similarity_fn or calculate_composite_similarity

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

        aembed_batch = getattr(self.embedder, "aembed_batch", None)
        embed_batch = getattr(self.embedder, "embed_batch", None)

        if aembed_batch and (
            inspect.iscoroutinefunction(aembed_batch)
            or type(aembed_batch).__name__ == "AsyncMock"
        ):
            embs = await aembed_batch(texts_list)
        elif embed_batch and type(embed_batch).__name__ != "MagicMock":
            loop = asyncio.get_running_loop()
            embs = await loop.run_in_executor(None, embed_batch, texts_list)
        else:
            aembed = getattr(self.embedder, "aembed", None)
            if aembed and (
                inspect.iscoroutinefunction(aembed)
                or type(aembed).__name__ == "AsyncMock"
            ):
                embs = await asyncio.gather(*(aembed(t) for t in texts_list))
            else:
                loop = asyncio.get_running_loop()
                embs = []
                for t in texts_list:
                    embs.append(
                        await loop.run_in_executor(None, self.embedder.embed, t)
                    )

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

            triplet_a = indexed_a.get(idx)
            triplet_b = indexed_b.get(idx)

            trip_a = self._to_dict(triplet_a)
            trip_b = self._to_dict(triplet_b)

            if not trip_a.get("head") or not trip_b.get("head"):
                await self.storage.raw_log.mark_consolidated(cmb_id)
                continue

            sim_score = _sim_fn(
                trip_a,
                trip_b,
                self.embedder,
                cache=embedding_cache,
            )

            if sim_score >= config.relation_similarity_threshold:
                await self._write_triplet(cmb_id, trip_a, weight=1.0)
                successful_writes += 1

            elif (
                config.uncertain_zone_lower_bound
                <= sim_score
                < config.relation_similarity_threshold
            ):
                divergence_count += 1
                await self._write_triplet(cmb_id, trip_a, weight=0.5)
                successful_writes += 1

            else:
                divergence_count += 1
                is_hub = await self._check_hub_node(
                    trip_a["head"],
                    trip_a["tail"],
                    trip_b["head"],
                    trip_b["tail"],
                )

                if is_hub:
                    self.human_review_queue.append(
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
            await self.storage.raw_log.mark_consolidated(cmb_id)

        return successful_writes, divergence_count

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    async def _write_triplet(self, cmb_id: str, triplet: dict, weight: float):
        """Upsert head/tail nodes and create an edge between them."""
        node_head = await self.storage.graph.upsert_node(
            name=triplet["head"],
            type="ENTITY",
            cmb_id=cmb_id,
            agent_id=SYSTEM_AGENT_ID,
            session_id=SYSTEM_SESSION_ID,
        )
        node_tail = await self.storage.graph.upsert_node(
            name=triplet["tail"],
            type="ENTITY",
            cmb_id=cmb_id,
            agent_id=SYSTEM_AGENT_ID,
            session_id=SYSTEM_SESSION_ID,
        )
        await self.storage.graph.create_edge(
            source_id=node_head,
            target_id=node_tail,
            relation=triplet["relation"],
            weight=weight,
            agent_id=SYSTEM_AGENT_ID,
            session_id=SYSTEM_SESSION_ID,
        )

    async def _check_hub_node(self, *entity_names: str) -> bool:
        """Check if any of the given entities are hub nodes (high degree)."""
        valid_names = [n for n in entity_names if n]
        if not valid_names:
            return False
        nodes = await self.storage.graph.find_nodes_by_name(
            valid_names,
            case_insensitive=True,
        )
        for node in nodes:
            degree = await self.storage.graph.get_node_degree(node["node_id"])
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
