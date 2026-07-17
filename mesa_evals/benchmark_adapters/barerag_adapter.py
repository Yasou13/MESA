# MESA v0.6.0 — BareRAGClient (Dumb Baseline)
# Pure vector-only retrieval with NO graph relations, NO time-awareness,
# and NO contradiction resolution logic.  Intentionally naive to serve
# as the control group in the Antigravity Contradiction Benchmark.
"""
Bare-bones RAG client for contradiction benchmark baselines.

This client uses **only** cosine similarity over dense embeddings.
It has zero awareness of:
  - Temporal ordering (t0 vs t1)
  - Graph relations or entity linking
  - Contradiction or conflict resolution
  - Semantic deduplication

It is designed to be easily fooled by "Red Herring" scenarios where
a semantically similar but legally irrelevant document (t1) appears
newer and cosine-closer to the query than the actually valid context.

Storage backend: LanceDB (disk-backed, per-agent isolated tables).
Embedding model: Sentence-Transformers (all-MiniLM-L6-v2) or any ``BaseUniversalLLMAdapter`` implementation.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from mesa_evals.benchmark_adapters.base import BaseMemoryClient, QueryResult
from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_storage.vector_engine import VectorEngine

logger = logging.getLogger("MESA_BareRAG")

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

_DEFAULT_STORAGE_ROOT = "./storage/benchmark_barerag"
_DEFAULT_SEARCH_LIMIT = 5  # Enforce identical Top-K=5 limit for benchmark fairness
_DISTANCE_THRESHOLD = 0.85  # Reject chunks with cosine distance > this


class BareRAGClient(BaseMemoryClient):
    """Pure cosine-similarity RAG client — the intentionally dumb baseline.

    Architecture:
        1. Ingest: embed text → upsert into LanceDB (agent_id scoped).
        2. Query: embed question → cosine search → concatenate top-K chunks.
        3. Clear: soft-delete all agent records + purge disk cache.

    Guarantees:
        - Strict agent_id isolation via LanceDB WHERE filters.
        - Zero graph relations, zero time ordering, zero contradiction logic.
        - Graceful degradation: never raises on query — returns
          ``QueryResult.empty()`` or ``QueryResult.from_error()``.

    Args:
        adapter: Embedding model adapter (any ``BaseUniversalLLMAdapter``).
        storage_root: Disk path for LanceDB data (default: ``./storage/benchmark_barerag``).
        search_limit: Default top-K for similarity search.
            In benchmark mode, this defaults to 1 to prevent context stuffing
            (retrieving both t0 and t1, offloading contradiction resolution
            to the LLM judge's context window instead of testing retrieval).
    """

    def __init__(
        self,
        adapter: BaseUniversalLLMAdapter,
        *,
        storage_root: str = _DEFAULT_STORAGE_ROOT,
        search_limit: int = _DEFAULT_SEARCH_LIMIT,
    ) -> None:
        self._adapter = adapter
        self._storage_root = storage_root
        self._search_limit = search_limit
        self._vector_engine: VectorEngine | None = None

        # In-memory content store: node_id → raw text.
        # BareRAG has no SQLite — this is the only way to recover
        # chunk text from a vector search hit.
        self._content_store: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Boot the LanceDB vector engine."""
        if self._vector_engine is not None and self._vector_engine.is_initialized:
            return

        self._vector_engine = VectorEngine(
            self._storage_root,
            metric="cosine",
        )
        await self._vector_engine.initialize()
        logger.info(
            "BARERAG_INIT | storage=%s adapter=%s",
            self._storage_root,
            type(self._adapter).__name__,
        )

    async def shutdown(self) -> None:
        """Close the LanceDB connection."""
        if self._vector_engine is not None:
            await self._vector_engine.close()
            self._vector_engine = None
        self._content_store.clear()
        logger.info("BARERAG_SHUTDOWN | storage released")

    # ------------------------------------------------------------------
    # Core contract: add_memory
    # ------------------------------------------------------------------

    async def add_memory(
        self,
        content: str,
        *,
        agent_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Embed and store a single text chunk.

        No entity extraction, no graph linking, no deduplication.
        Just embed → upsert → done.
        """
        self._validate_agent_id(agent_id)
        self._assert_initialized()

        node_id = str(uuid.uuid4())
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Generate embedding via VectorEngine
        assert self._vector_engine is not None
        embedding = await self._vector_engine.compute_embedding(content)

        # Upsert into LanceDB with strict agent_id isolation
        assert self._vector_engine is not None
        await self._vector_engine.upsert(
            node_id=node_id,
            agent_id=agent_id,
            embedding=embedding,
            content_hash=content_hash,
        )

        # Store raw text in memory (BareRAG has no relational layer)
        self._content_store[node_id] = {
            "content": content,
            "agent_id": agent_id,
            "metadata": metadata or {},
        }

        logger.debug(
            "BARERAG_ADD | agent_id=%s node_id=%s len=%d dim=%d",
            agent_id,
            node_id,
            len(content),
            len(embedding),
        )
        return node_id

    # ------------------------------------------------------------------
    # Core contract: query
    # ------------------------------------------------------------------

    async def query(
        self,
        question: str,
        *,
        agent_id: str,
        limit: int | None = None,
    ) -> QueryResult:
        """Pure cosine similarity search — no graph, no time, no logic.

        Embeds the question, searches LanceDB with agent_id filter,
        concatenates the top-K chunks, and returns them as-is.
        A naive system will rank by cosine distance alone, making it
        trivially susceptible to recency bias and red herrings.

        Never raises — returns ``QueryResult.empty()`` on zero results
        or ``QueryResult.from_error()`` on exceptions.
        """
        self._validate_agent_id(agent_id)

        if self._vector_engine is None or not self._vector_engine.is_initialized:
            return QueryResult.from_error("BareRAGClient not initialized")

        k = limit or self._search_limit

        try:
            # Step 1: Embed the query via VectorEngine
            query_embedding = await self._vector_engine.compute_embedding(question)

            # Step 2: Cosine similarity search (agent_id hardcoded in WHERE)
            raw_results = await self._vector_engine.search(
                query_vector=query_embedding,
                limit=k,
                agent_id=agent_id,
                include_expired=False,
            )
        except Exception as exc:
            logger.warning(
                "BARERAG_QUERY_ERROR | agent_id=%s error=%s",
                agent_id,
                exc,
            )
            return QueryResult.from_error(f"Search failed: {exc}")

        if not raw_results:
            return QueryResult.empty()

        # Step 3: Reconstruct text from content store
        chunks: list[dict[str, Any]] = []
        context_parts: list[str] = []

        for hit in raw_results:
            node_id = hit.get("node_id", "")
            distance = hit.get("_distance", 1.0)

            # Reject chunks beyond the distance threshold
            if distance > _DISTANCE_THRESHOLD:
                continue

            stored = self._content_store.get(node_id)
            if stored and stored.get("agent_id") == agent_id:
                chunk_text = stored["content"]
                context_parts.append(chunk_text)
                chunks.append(
                    {
                        "node_id": node_id,
                        "content": chunk_text,
                        "distance": round(distance, 6),
                        "metadata": stored.get("metadata", {}),
                    }
                )

        if not context_parts:
            return QueryResult.empty()

        # Concatenate chunks with separator (no reranking, no logic)
        combined_context = "\n---\n".join(context_parts)

        return QueryResult(
            context=combined_context,
            chunks=chunks,
            total_chunks=len(chunks),
            error=None,
        )

    # ------------------------------------------------------------------
    # Core contract: clear_memory
    # ------------------------------------------------------------------

    async def clear_memory(self, *, agent_id: str) -> int:
        """Purge all data for the given agent_id.

        Performs a two-phase cleanup:
          1. Soft-delete all vector records in LanceDB for this agent.
          2. Evict all in-memory content entries for this agent.
        """
        self._validate_agent_id(agent_id)

        if self._vector_engine is None or not self._vector_engine.is_initialized:
            return 0

        # Phase 1: Find and soft-delete all vector records for this agent
        try:
            active_ids = await self._vector_engine.get_active_node_ids(
                agent_id=agent_id,
            )
        except Exception as exc:
            logger.warning(
                "BARERAG_CLEAR_ERROR | agent_id=%s get_ids_error=%s",
                agent_id,
                exc,
            )
            active_ids = set()

        purged = 0
        for node_id in active_ids:
            try:
                await self._vector_engine.soft_delete(node_id, agent_id)
                purged += 1
            except Exception as exc:
                logger.warning(
                    "BARERAG_CLEAR_SOFT_DELETE_ERROR | node_id=%s error=%s",
                    node_id,
                    exc,
                )

        # Phase 2: Evict in-memory content for this agent
        evict_ids = [
            nid
            for nid, data in self._content_store.items()
            if data.get("agent_id") == agent_id
        ]
        for nid in evict_ids:
            del self._content_store[nid]

        logger.info(
            "BARERAG_CLEAR | agent_id=%s purged_vectors=%d evicted_content=%d",
            agent_id,
            purged,
            len(evict_ids),
        )
        return purged + len(evict_ids)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_initialized(self) -> None:
        """Guard against operations on an uninitialised engine."""
        if self._vector_engine is None or not self._vector_engine.is_initialized:
            raise RuntimeError(
                "BareRAGClient has not been initialized. "
                "Call `await client.initialize()` first."
            )

    def __repr__(self) -> str:
        return (
            f"<BareRAGClient storage={self._storage_root!r} "
            f"adapter={type(self._adapter).__name__}>"
        )
