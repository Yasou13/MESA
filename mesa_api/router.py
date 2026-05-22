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
from typing import Protocol, Sequence, runtime_checkable

from fastapi import APIRouter, BackgroundTasks, HTTPException

from mesa_api.schemas import (
    ErrorResponse,
    MemoryInsertRequest,
    MemoryPurgeRequest,
    MemorySearchRequest,
)
from mesa_storage.dao import MemoryDAO

logger = logging.getLogger("MESA_API")


# ---------------------------------------------------------------------------
# Embedding function protocol (pluggable)
# ---------------------------------------------------------------------------


@runtime_checkable
class EmbedderProtocol(Protocol):
    """Structural interface for an embedding function."""

    def __call__(self, text: str) -> list[float]: ...


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
    dao: MemoryDAO,
    *,
    embedder: EmbedderProtocol = _noop_embedder,
    prefix: str = "/v3/memory",
    tags: Sequence[str] | None = None,
) -> APIRouter:
    """Create a FastAPI APIRouter with MESA v3 memory endpoints.

    Args:
        dao: Initialized MemoryDAO instance.
        embedder: Callable that converts text → float vector.
        prefix: URL prefix for all routes (default: /v3/memory).
        tags: OpenAPI tags for documentation grouping.

    Returns:
        A configured APIRouter instance ready for ``app.include_router()``.
    """
    router = APIRouter(
        prefix=prefix,
        tags=list(tags) if tags else ["memory"],
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
            dao=dao,
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
            fts_results = await dao.search_memory_fts(
                agent_id=request.agent_id,
                query=request.query,
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

                # Bi-temporal read path logic
                if not node.get("is_consolidated", True):
                    context_parts[-1] += " [WARNING: UNCONSOLIDATED MEMORY]"

            # Phase 2: Vector similarity search (if engine is available)
            if dao.vector_engine is not None and dao.vector_engine.is_initialized:
                query_embedding = embedder(request.query)
                vec_results = await dao.search_memory(
                    agent_id=request.agent_id,
                    query_vector=query_embedding,
                    limit=request.limit,
                    include_graph=False,
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
            session_id = request.scope_id if request.scope == "session" else None
            deleted_count = await dao.purge_memory(
                agent_id=request.agent_id,
                scope=request.scope,
                session_id=session_id,
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
    dao: MemoryDAO,
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
        embedding = embedder(content)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        await dao.insert_memory(
            agent_id=agent_id,
            node_id=memory_id,
            entity_name=content[:256],
            content=content,
            embedding=embedding,
            node_type="MEMORY",
            session_id=session_id,
            content_hash=content_hash,
            metadata=metadata,
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
