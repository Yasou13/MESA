# MESA v0.3.0 — Phase 1: Headless API Routers
# Asynchronous FastAPI router with three endpoints for the v3 API surface.
#
# Architecture:
#   - POST /v3/memory/insert  → fire-and-forget via BackgroundTasks (<150ms)
#   - POST /v3/memory/search  → synchronous await on DAO retrieval
#   - DELETE /v3/memory/purge → soft-delete ONLY — NO VACUUM, NO hard-delete
#
# Critical constraint:
#   The purge endpoint MUST NOT trigger VACUUM or hard-delete operations.
#   Physical removal is exclusively handled by the MaintenanceWorker.
#   Violating this invariant causes catastrophic WAL locks under load.
"""
Headless FastAPI v3 API routers for the MESA memory system.

All endpoints enforce strict Pydantic V2 validation via the schemas
in ``mesa_api.schemas``.  The insert endpoint is optimised for hot-path
latency by deferring the actual write to a ``BackgroundTasks`` queue
and returning immediately with a pre-generated UUID.

Endpoints::

    POST   /v3/memory/insert  — Queue memory ingestion (< 150ms response)
    POST   /v3/memory/search  — Synchronous retrieval with latency metrics
    DELETE /v3/memory/purge   — Soft-delete ONLY (no VACUUM, no hard-delete)

Usage::

    from mesa_api.router import create_memory_router

    router = create_memory_router(
        sqlite_engine=engine,
        vector_engine=vec_engine,
    )
    app.include_router(router)
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter, BackgroundTasks, HTTPException

from mesa_api.schemas import (
    ErrorResponse,
    MemoryInsertRequest,
    MemoryPurgeRequest,
    MemorySearchRequest,
)

logger = logging.getLogger("MESA_API")


# ---------------------------------------------------------------------------
# Storage protocol — structural typing for engine dependencies
# ---------------------------------------------------------------------------


@runtime_checkable
class SQLiteEngineProtocol(Protocol):
    """Structural interface for the AsyncEngine dependency."""

    @property
    def db_path(self) -> str:
        ...

    @property
    def is_initialized(self) -> bool:
        ...

    async def initialize(self) -> None:
        ...

    def connection(self) -> Any:
        ...

    def transaction(self) -> Any:
        ...


@runtime_checkable
class VectorEngineProtocol(Protocol):
    """Structural interface for the VectorEngine dependency."""

    @property
    def is_initialized(self) -> bool:
        ...

    async def initialize(self) -> None:
        ...

    async def upsert(
        self,
        node_id: str,
        agent_id: str,
        embedding: list[float],
        content_hash: str | None = None,
    ) -> None:
        ...

    async def search(
        self,
        query_vector: list[float],
        *,
        limit: int = 10,
        agent_id: str | None = None,
        include_expired: bool = False,
    ) -> list[dict]:
        ...

    async def soft_delete(self, node_id: str) -> None:
        ...


# ---------------------------------------------------------------------------
# Embedding function protocol (pluggable)
# ---------------------------------------------------------------------------


@runtime_checkable
class EmbedderProtocol(Protocol):
    """Structural interface for an embedding function."""

    def __call__(self, text: str) -> list[float]:
        ...


def _noop_embedder(text: str) -> list[float]:
    """Placeholder embedder — returns a zero vector.

    In production, this should be replaced with a real embedding model
    via ``create_memory_router(embedder=my_embed_fn)``.
    """
    return [0.0] * 8


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_memory_router(
    sqlite_engine: SQLiteEngineProtocol,
    vector_engine: VectorEngineProtocol | None = None,
    *,
    embedder: EmbedderProtocol = _noop_embedder,
    prefix: str = "/v3/memory",
    tags: list[str] | None = None,
) -> APIRouter:
    """Create a FastAPI APIRouter with MESA v3 memory endpoints.

    Args:
        sqlite_engine: Initialized AsyncEngine for graph operations.
        vector_engine: Optional initialized VectorEngine for vector ops.
        embedder: Callable that converts text → float vector.
        prefix: URL prefix for all routes (default: /v3/memory).
        tags: OpenAPI tags for documentation grouping.

    Returns:
        A configured APIRouter instance ready for ``app.include_router()``.
    """
    router = APIRouter(
        prefix=prefix,
        tags=tags or ["memory"],
        responses={
            422: {"model": ErrorResponse, "description": "Validation Error"},
            500: {"model": ErrorResponse, "description": "Internal Error"},
        },
    )

    # ==================================================================
    # POST /v3/memory/insert
    # ==================================================================

    @router.post(
        "/insert",
        status_code=202,
        summary="Queue memory insertion",
        response_description="Acknowledged with pre-generated memory_id",
    )
    async def insert_memory(
        request: MemoryInsertRequest,
        background_tasks: BackgroundTasks,
    ) -> dict:
        """Queue a memory record for asynchronous ingestion.

        **Hot-path optimisation**: Returns immediately with a pre-generated
        UUID.  The actual database write is offloaded to FastAPI's
        ``BackgroundTasks`` queue to guarantee < 150ms response latency.

        The background task:
          1. Computes the content embedding via the configured embedder.
          2. Inserts a graph node into SQLite.
          3. Upserts the embedding vector into LanceDB (if configured).
        """
        memory_id = uuid.uuid4().hex

        background_tasks.add_task(
            _background_insert,
            sqlite_engine=sqlite_engine,
            vector_engine=vector_engine,
            embedder=embedder,
            memory_id=memory_id,
            agent_id=request.agent_id,
            session_id=request.session_id,
            content=request.content,
            metadata=request.metadata,
        )

        return {"status": "queued", "memory_id": memory_id}

    # ==================================================================
    # POST /v3/memory/search
    # ==================================================================

    @router.post(
        "/search",
        summary="Search memory",
        response_description="Retrieved context with latency metrics",
    )
    async def search_memory(request: MemorySearchRequest) -> dict:
        """Execute a synchronous memory search and return results.

        Performs a two-phase retrieval:
          1. FTS5 lexical pre-filter on the SQLite graph (zero-VRAM).
          2. Cosine similarity search on the LanceDB vector index.

        Results are merged and returned with server-side latency metrics.
        """
        t_start = time.monotonic()

        retrieved_nodes: list[dict] = []
        context_parts: list[str] = []

        try:
            # Phase 1: FTS5 lexical pre-filter (zero-VRAM)
            from mesa_storage.schemas import fts5_search

            fts_results = await fts5_search(
                sqlite_engine,
                request.query,
                agent_id=request.agent_id,
                limit=request.limit,
            )

            for node in fts_results:
                retrieved_nodes.append(
                    {
                        "node_id": node["id"],
                        "entity_name": node["entity_name"],
                        "type": node.get("type", "ENTITY"),
                        "source": "fts5",
                        "agent_id": node.get("agent_id", ""),
                    }
                )
                context_parts.append(node["entity_name"])

            # Phase 2: Vector similarity search (if engine is available)
            if vector_engine is not None and vector_engine.is_initialized:
                query_embedding = embedder(request.query)
                vec_results = await vector_engine.search(
                    query_vector=query_embedding,
                    limit=request.limit,
                    agent_id=request.agent_id,
                )

                # Merge vector results, dedup by node_id
                seen_ids = {n["node_id"] for n in retrieved_nodes}
                for vr in vec_results:
                    if vr["node_id"] not in seen_ids:
                        retrieved_nodes.append(
                            {
                                "node_id": vr["node_id"],
                                "agent_id": vr.get("agent_id", ""),
                                "score": vr.get("_distance", 0.0),
                                "source": "vector",
                                "content_hash": vr.get("content_hash"),
                            }
                        )
                        seen_ids.add(vr["node_id"])

        except Exception as exc:
            logger.error(
                "SEARCH_ERROR | agent_id=%s query=%r error=%s",
                request.agent_id,
                request.query[:50],
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Search failed: {type(exc).__name__}",
            )

        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        context = "; ".join(context_parts) if context_parts else ""

        return {
            "context": context,
            "retrieved_nodes": retrieved_nodes[: request.limit],
            "metrics": {"latency_ms": elapsed_ms},
        }

    # ==================================================================
    # DELETE /v3/memory/purge
    # ==================================================================

    @router.delete(
        "/purge",
        summary="Soft-delete memory records",
        response_description="Purge result with affected record count",
    )
    async def purge_memory(request: MemoryPurgeRequest) -> dict:
        """Soft-delete memory records by agent or session scope.

        **CRITICAL**: This endpoint performs ONLY soft-deletes.
        It MUST NOT trigger VACUUM, hard-delete, or any compaction
        operation.  Physical removal is exclusively handled by the
        ``MaintenanceWorker`` during scheduled idle windows.

        Violating this invariant causes catastrophic WAL locks under
        concurrent API load.
        """
        deleted_count = 0

        try:
            if request.scope == "agent":
                deleted_count = await _soft_delete_by_agent(
                    sqlite_engine, vector_engine, request.agent_id
                )
            else:
                deleted_count = await _soft_delete_by_session(
                    sqlite_engine,
                    vector_engine,
                    request.agent_id,
                    request.scope_id,
                )

        except Exception as exc:
            logger.error(
                "PURGE_ERROR | agent_id=%s scope=%s error=%s",
                request.agent_id,
                request.scope,
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Purge failed: {type(exc).__name__}",
            )

        return {
            "status": "purged",
            "deleted_records_count": deleted_count,
        }

    return router


# ---------------------------------------------------------------------------
# Background task: deferred insert
# ---------------------------------------------------------------------------


async def _background_insert(
    *,
    sqlite_engine: SQLiteEngineProtocol,
    vector_engine: VectorEngineProtocol | None,
    embedder: EmbedderProtocol,
    memory_id: str,
    agent_id: str,
    session_id: str,
    content: str,
    metadata: dict,
) -> None:
    """Execute the actual database write in the background.

    This runs after the HTTP response has already been sent.
    Failures are logged but do not affect the client response.
    """
    try:
        # Step 1: Insert graph node into SQLite
        from mesa_storage.schemas import insert_node

        await insert_node(
            sqlite_engine,
            node_id=memory_id,
            entity_name=content[:256],  # Truncate for entity_name column
            node_type="MEMORY",
            agent_id=agent_id,
            session_id=session_id,
        )

        # Step 2: Compute embedding and upsert into vector store
        if vector_engine is not None and vector_engine.is_initialized:
            embedding = embedder(content)
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            await vector_engine.upsert(
                node_id=memory_id,
                agent_id=agent_id,
                embedding=embedding,
                content_hash=content_hash,
            )

        logger.info(
            "BACKGROUND_INSERT_OK | memory_id=%s agent_id=%s",
            memory_id,
            agent_id,
        )

    except Exception as exc:
        # Background task failure — log but do NOT propagate.
        # The client already received 202 Accepted.
        logger.error(
            "BACKGROUND_INSERT_FAILED | memory_id=%s agent_id=%s error=%s",
            memory_id,
            agent_id,
            exc,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Purge helpers — SOFT-DELETE ONLY
# ---------------------------------------------------------------------------


async def _soft_delete_by_agent(
    sqlite_engine: SQLiteEngineProtocol,
    vector_engine: VectorEngineProtocol | None,
    agent_id: str,
) -> int:
    """Soft-delete ALL records for an agent. NO VACUUM. NO HARD-DELETE.

    Returns:
        Total number of records soft-deleted.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    deleted = 0

    async with sqlite_engine.connection() as db:
        # Soft-delete edges owned by this agent
        cursor = await db.execute(
            "UPDATE edges SET invalid_at = ? "
            "WHERE agent_id = ? AND invalid_at IS NULL",
            (now, agent_id),
        )
        deleted += cursor.rowcount

        # Soft-delete nodes owned by this agent
        cursor = await db.execute(
            "UPDATE nodes SET invalid_at = ? "
            "WHERE agent_id = ? AND invalid_at IS NULL",
            (now, agent_id),
        )
        deleted += cursor.rowcount
        await db.commit()

    # Soft-delete vector records (mark as expired — NOT hard-delete)
    if vector_engine is not None and vector_engine.is_initialized:
        try:
            active_ids = await vector_engine.get_active_node_ids(agent_id=agent_id)
            for node_id in active_ids:
                await vector_engine.soft_delete(node_id)
                deleted += 1
        except Exception as exc:
            logger.warning(
                "VECTOR_SOFT_DELETE_PARTIAL | agent_id=%s error=%s",
                agent_id,
                exc,
            )

    return deleted


async def _soft_delete_by_session(
    sqlite_engine: SQLiteEngineProtocol,
    vector_engine: VectorEngineProtocol | None,
    agent_id: str,
    session_id: str,
) -> int:
    """Soft-delete records for a specific session. NO VACUUM. NO HARD-DELETE.

    Returns:
        Total number of records soft-deleted.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    deleted = 0

    async with sqlite_engine.connection() as db:
        # Get node IDs for this session to cascade to edges
        async with db.execute(
            "SELECT id FROM nodes "
            "WHERE agent_id = ? AND session_id = ? AND invalid_at IS NULL",
            (agent_id, session_id),
        ) as cursor:
            node_rows = await cursor.fetchall()
            node_ids = [row[0] for row in node_rows]

        if node_ids:
            # Soft-delete edges connected to these nodes
            placeholders = ",".join("?" for _ in node_ids)
            cursor = await db.execute(
                f"UPDATE edges SET invalid_at = ? "
                f"WHERE (source_id IN ({placeholders}) "
                f"OR target_id IN ({placeholders})) "
                f"AND invalid_at IS NULL",
                [now] + node_ids + node_ids,
            )
            deleted += cursor.rowcount

        # Soft-delete the session's nodes
        cursor = await db.execute(
            "UPDATE nodes SET invalid_at = ? "
            "WHERE agent_id = ? AND session_id = ? AND invalid_at IS NULL",
            (now, agent_id, session_id),
        )
        deleted += cursor.rowcount
        await db.commit()

    # Soft-delete vector records for purged nodes
    if vector_engine is not None and vector_engine.is_initialized and node_ids:
        try:
            for node_id in node_ids:
                await vector_engine.soft_delete(node_id)
                deleted += 1
        except Exception as exc:
            logger.warning(
                "VECTOR_SOFT_DELETE_PARTIAL | session_id=%s error=%s",
                session_id,
                exc,
            )

    return deleted
