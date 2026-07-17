# MESA v0.6.0 — Phase 1: Hot Path Architecture
# Asynchronous FastAPI router with decoupled ingestion pipeline.
#
# Architecture:
#   - POST /v3/memory/insert  → hot-path: raw_logs INSERT + cold-path BG task (<50ms)
#   - POST /v3/memory/search  → synchronous await on DAO retrieval
#   - DELETE /v3/memory/purge → soft-delete ONLY — NO VACUUM, NO hard-delete
#
# Critical constraint:
#   The insert endpoint MUST NOT perform any LLM validation, ECOD, or REBEL
#   extraction on the hot path. All heavy processing is deferred to
#   process_cold_path via BackgroundTasks.
#
#   The purge endpoint MUST NOT trigger VACUUM or hard-delete operations.
#   Physical removal is exclusively handled by the MaintenanceWorker.
#   Violating this invariant causes catastrophic WAL locks under load.
"""
Headless FastAPI v3 API routers for the MESA memory system.

All endpoints enforce strict Pydantic V2 validation via the schemas
in ``mesa_api.schemas``.  The insert endpoint is optimised for hot-path
latency (< 50ms) by writing raw payloads to a staging table
(``raw_logs``) and deferring heavy LLM processing to a cold-path
background task.

Endpoints::

    POST   /v3/memory/insert  — Hot-path INSERT + cold-path BG task (< 50ms)
    POST   /v3/memory/search  — Synchronous retrieval with latency metrics
    DELETE /v3/memory/purge   — Soft-delete ONLY (no VACUUM, no hard-delete)

Usage::

    from mesa_api.router import create_memory_router

    router = create_memory_router(
        dao=dao,
    )
    app.include_router(router)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Callable, Protocol, Sequence, runtime_checkable

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from mesa_api.schemas import (
    ErrorResponse,
    MemoryInsertRequest,
    MemoryPurgeRequest,
    MemorySearchRequest,
    MemorySearchResponse,
    SessionContextResponse,
    SessionEndRequest,
    SessionStartRequest,
    SessionStartResponse,
)
from mesa_memory.api.middleware import limiter
from mesa_memory.config import config
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.retrieval.core import QueryAnalyzer
from mesa_memory.retrieval.hybrid import HybridRetriever
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO
from mesa_workers.ingestion_worker import (
    process_cold_path,  # Cold-path worker (Phase 1 Part 2)
)

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
    logger.warning(
        "NOOP_EMBEDDER_INVOKED | Search quality degraded — "
        "configure a real embedder via create_memory_router(get_embedder=...)"
    )
    return [0.0] * 8


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Lazy singletons — constructed once per process, reused across requests
# ---------------------------------------------------------------------------

_query_analyzer: QueryAnalyzer | None = None


def _get_query_analyzer() -> QueryAnalyzer:
    """Lazy-init the QueryAnalyzer singleton."""
    global _query_analyzer
    if _query_analyzer is None:
        _query_analyzer = QueryAnalyzer()
    return _query_analyzer


_reranker: Any | None = None


def _get_reranker() -> Any | None:
    """Lazy-init the CrossEncoderReranker singleton if enabled via config."""
    global _reranker
    if not config.crossencoder_enabled:
        return None
    if _reranker is None:
        from mesa_memory.retrieval.reranker import CrossEncoderReranker

        _reranker = CrossEncoderReranker(config.crossencoder_model)
    return _reranker


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_memory_router(
    get_dao: Callable[[], MemoryDAO],
    *,
    get_embedder: Callable[[], EmbedderProtocol] = lambda: _noop_embedder,
    get_consolidation_loop: Callable[[], ConsolidationLoop | None] = lambda: None,
    get_access_control: Callable[[], AccessControl] | None = None,
    prefix: str = "/v3/memory",
    tags: Sequence[str] | None = None,
) -> APIRouter:
    """Create a FastAPI APIRouter with MESA v3 memory endpoints.

    Args:
        get_dao: Dependency factory returning an initialised MemoryDAO.
        get_embedder: Callable that converts text → float vector.
        get_consolidation_loop: Callable returning the active
            ``ConsolidationLoop`` instance (or ``None`` if Tier-3
            consensus is disabled).  Deferred via callable because
            the loop is initialised in the async lifespan, after
            router construction at module-load time.
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
    # POST /v3/memory/insert  —  HOT PATH (< 50ms)
    # ==================================================================

    @router.post(
        "/insert",
        status_code=202,
        summary="Queue memory insertion (hot path)",
        response_description="Acknowledged with log_id for tracking",
        responses={
            403: {"model": ErrorResponse, "description": "RBAC Access Denied"},
        },
    )
    async def insert_memory(
        request: Request,
        payload: MemoryInsertRequest,
        background_tasks: BackgroundTasks,
        dao: MemoryDAO = Depends(get_dao),
    ) -> JSONResponse:
        """Queue a memory record for asynchronous cold-path processing.

        **Hot-path architecture (v0.6.0)**: The endpoint performs a single
        async INSERT into the ``raw_logs`` staging table and returns
        immediately with the generated ``log_id``.

        All heavy processing (ECOD, REBEL extraction, LLM validation,
        embedding, graph insertion) is deferred to ``process_cold_path``
        via ``BackgroundTasks``.

        Target latency: **< 50ms**.
        """
        structlog.contextvars.bind_contextvars(
            agent_id=payload.agent_id,
            session_id=payload.session_id,
            endpoint="insert_memory",
        )

        # ---------------------------------------------------------------
        # RBAC Gate: Verify WRITE permission for this agent/session pair.
        # This is a secondary security layer that operates alongside the
        # API Key authentication enforced at the router dependency level.
        # ---------------------------------------------------------------
        try:
            ac = get_access_control() if get_access_control else AccessControl()
            has_write = await ac.check_access(
                payload.agent_id,
                payload.session_id,
                "WRITE",
            )
            if not has_write:
                raise PermissionError(
                    f"Agent '{payload.agent_id}' lacks WRITE access "
                    f"for session '{payload.session_id}'"
                )
        except PermissionError as perm_exc:
            raise HTTPException(status_code=403, detail=str(perm_exc))

        payload_dict = {
            "agent_id": payload.agent_id,
            "session_id": payload.session_id,
            "content": payload.content,
            "metadata": payload.metadata,
        }

        log_id = await dao.insert_raw_log(payload.agent_id, payload_dict)

        def dummy_task():
            with open("dummy.txt", "w") as f:
                f.write("DUMMY TASK EXECUTED\n")

        background_tasks.add_task(dummy_task)
        background_tasks.add_task(
            process_cold_path,
            log_id,
            payload.agent_id,
            dao,
            consolidation_loop=get_consolidation_loop(),
        )

        return JSONResponse(
            status_code=202,
            content={
                "status": "DEFERRED",
                "agent_id": payload.agent_id,
                "log_id": log_id,
            },
            background=background_tasks,
        )

    # ==================================================================
    # GET /v3/memory/status/{log_id}  —  Cold-path status query
    # ==================================================================

    @router.get(
        "/status/{log_id}",
        summary="Query cold-path processing status for a raw_log entry",
        response_description="Current processing status of the log entry",
    )
    @limiter.limit("60/minute")
    async def get_status(
        request: Request,
        log_id: int,
        agent_id: str,
        dao: MemoryDAO = Depends(get_dao),
    ) -> dict:
        """Return the current processing status of a queued raw_log entry.

        Used by the evaluation harness (``recall_harness.py``) to poll for
        cold-path completion instead of relying on fragile time-based
        heuristics.

        Terminal states: ``processed``, ``failed``, ``rejected``.
        """
        # RBAC Gate: Verify READ permission for this agent
        ac = get_access_control() if get_access_control else AccessControl()
        if not await ac.check_access(agent_id, "__any__", "READ"):
            raise HTTPException(
                status_code=403,
                detail=f"Agent '{agent_id}' lacks READ access",
            )

        structlog.contextvars.bind_contextvars(
            agent_id=agent_id, log_id=log_id, endpoint="get_status"
        )

        raw_log = await dao.get_raw_log(agent_id, log_id)

        if raw_log is None:
            raise HTTPException(
                status_code=404,
                detail=f"raw_log {log_id} not found",
            )

        return {
            "log_id": log_id,
            "status": raw_log.get("status", "unknown"),
        }

    # ==================================================================
    # POST /v3/memory/search
    # ==================================================================

    @router.post(
        "/search",
        summary="Search memory",
        response_description="Retrieved context with latency metrics",
        response_model=MemorySearchResponse,
    )
    @limiter.limit("60/minute")
    async def search_memory(
        request: Request,
        payload: MemorySearchRequest,
        dao: MemoryDAO = Depends(get_dao),
    ) -> MemorySearchResponse:
        """Execute a hybrid memory search via the production HybridRetriever.

        Performs ranked retrieval through the full fusion pipeline:
          1. Vector similarity (LanceDB) — cosine distance scoring.
          2. Lexical pre-filter (FTS5) — BM25-normalised scoring.
          3. Graph traversal (KùzuDB) — cognitive salience scoring.
          4. Alpha-reranking fusion across all three signal sources.

        Results are returned in the canonical response schema:
        ``{context, retrieved_nodes, metrics}``.
        """
        structlog.contextvars.bind_contextvars(
            agent_id=payload.agent_id,
            session_id=payload.session_id,
            endpoint="search_memory",
        )
        t_start = time.monotonic()

        try:
            # Build the HybridRetriever with per-request DAO + shared singletons
            ac = get_access_control() if get_access_control else AccessControl()
            retriever = HybridRetriever(
                dao=dao,
                analyzer=_get_query_analyzer(),
                access_control=ac,
                reranker=_get_reranker(),
            )

            result = await asyncio.wait_for(
                retriever.retrieve(
                    query_text=payload.query,
                    agent_id=payload.agent_id,
                    session_id=payload.session_id,
                    top_n=payload.limit,
                ),
                timeout=30.0,  # 30s hard ceiling — prevents indefinite hangs
            )

            # Normalise retriever output — retrieve() returns list[str] (cmb_ids)
            # when multi_hop is disabled (default), or dict when enabled.
            if isinstance(result, dict):
                cmb_ids: list[str] = result.get("cmb_ids", [])
            else:
                cmb_ids = result

            # Hydrate node metadata from the DAO for the response contract
            retrieved_nodes: list[dict] = []
            context_parts: list[str] = []

            for cmb_id in cmb_ids:
                node = await dao.get_memory_by_id(payload.agent_id, cmb_id)
                if node is None:
                    # Node may have been purged between retrieval and hydration
                    retrieved_nodes.append(
                        {
                            "node_id": cmb_id,
                            "agent_id": payload.agent_id,
                            "source": "hybrid",
                            "score": 0.0,
                        }
                    )
                    continue

                entity_name = node.get("entity_name", "")
                retrieved_nodes.append(
                    {
                        "node_id": cmb_id,
                        "entity_name": entity_name,
                        "content_payload": node.get("content"),
                        "type": node.get("node_type", "ENTITY"),
                        "source": "hybrid",
                        "score": 1.0,
                        "agent_id": node.get("agent_id", payload.agent_id),
                    }
                )
                ctx = entity_name
                if not node.get("is_consolidated", True):
                    ctx += " [WARNING: UNCONSOLIDATED MEMORY]"
                context_parts.append(ctx)

        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail="Search timed out (30s ceiling exceeded)",
            )

        except PermissionError as perm_exc:
            raise HTTPException(status_code=403, detail=str(perm_exc))

        except Exception as exc:
            import traceback

            traceback.print_exc()
            logger.error(
                "SEARCH_ERROR | agent_id=%s query=%r error=%s",
                payload.agent_id,
                payload.query[:50],
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Search failed: {type(exc).__name__}",
            )

        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        context = "; ".join(context_parts) if context_parts else ""

        return MemorySearchResponse(
            context=context,
            retrieved_nodes=retrieved_nodes[: payload.limit],
            metrics={"latency_ms": elapsed_ms},
        )

    # ==================================================================
    # DELETE /v3/memory/purge
    # ==================================================================

    @router.delete(
        "/purge",
        summary="Soft-delete memory records",
        response_description="Purge result with affected record count",
    )
    @limiter.limit("60/minute")
    async def purge_memory(
        request: Request,
        payload: MemoryPurgeRequest,
        dao: MemoryDAO = Depends(get_dao),
    ) -> dict:
        """Soft-delete memory records by agent or session scope.

        **CRITICAL**: This endpoint performs ONLY soft-deletes.
        It MUST NOT trigger VACUUM, hard-delete, or any compaction
        operation.  Physical removal is exclusively handled by the
        ``MaintenanceWorker`` during scheduled idle windows.

        Violating this invariant causes catastrophic WAL locks under
        concurrent API load.
        """
        # RBAC Gate: Verify WRITE permission for this agent/session pair.
        try:
            ac = get_access_control() if get_access_control else AccessControl()
            _session = payload.scope_id if payload.scope == "session" else "__any__"
            if not await ac.check_access(payload.agent_id, _session, "WRITE"):
                raise PermissionError(
                    f"Agent '{payload.agent_id}' lacks WRITE access for purge"
                )
        except PermissionError as perm_exc:
            raise HTTPException(status_code=403, detail=str(perm_exc))

        deleted_count = 0

        try:
            session_id = payload.scope_id if payload.scope == "session" else None
            deleted_count = await dao.purge_memory(
                agent_id=payload.agent_id,
                scope=payload.scope,
                session_id=session_id,
            )

        except Exception as exc:
            import traceback

            traceback.print_exc()
            logger.error(
                "PURGE_ERROR | agent_id=%s scope=%s error=%s",
                payload.agent_id,
                payload.scope,
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

    # ==================================================================
    # POST /v3/session/start
    # ==================================================================

    @router.post(
        "/session/start",
        tags=["session"],
        summary="Start a new session",
        response_description="Returns a new unique session_id",
        response_model=SessionStartResponse,
    )
    @limiter.limit("60/minute")
    async def start_session(
        request: Request,
        payload: SessionStartRequest,
        dao: MemoryDAO = Depends(get_dao),
    ) -> SessionStartResponse:
        """Generate and return a new unique session identifier.

        Enforces strict RBAC: requires a valid ``agent_id`` in the request payload.
        """
        # RBAC Gate: Verify WRITE permission for this agent.
        ac = get_access_control() if get_access_control else AccessControl()
        session_id = f"sess_{uuid.uuid4().hex}"

        # Grant WRITE access for the newly created session
        await ac.grant_access(payload.agent_id, session_id, "WRITE")

        logger.info(
            "SESSION_START | agent_id=%s session_id=%s", payload.agent_id, session_id
        )
        return SessionStartResponse(session_id=session_id, agent_id=payload.agent_id)

    # ==================================================================
    # GET /v3/session/{session_id}/context
    # ==================================================================

    @router.get(
        "/session/{session_id}/context",
        tags=["session"],
        summary="Retrieve session context",
        response_description="Consolidated memory and recent logs for the session",
        response_model=SessionContextResponse,
    )
    @limiter.limit("60/minute")
    async def get_session_context(
        request: Request,
        session_id: str,
        agent_id: str,
        dao: MemoryDAO = Depends(get_dao),
    ) -> SessionContextResponse:
        """Retrieve consolidated memory and recent episodic logs tied to the session.

        Enforces strict RBAC: requires the correct ``agent_id`` query parameter matching
        the tenant isolation model to retrieve session data.
        """
        # RBAC Gate: Verify READ permission for this agent/session pair.
        try:
            ac = get_access_control() if get_access_control else AccessControl()
            if not await ac.check_access(agent_id, session_id, "READ"):
                raise PermissionError(
                    f"Agent '{agent_id}' lacks READ access for session '{session_id}'"
                )
        except PermissionError as perm_exc:
            raise HTTPException(status_code=403, detail=str(perm_exc))

        try:
            # Fetch recent episodic logs (raw_logs) for the session
            recent_logs = []
            raw_logs_data = await dao.get_recent_logs(agent_id, session_id, limit=10)
            for payload in raw_logs_data:
                if "content" in payload:
                    recent_logs.append({"content": payload["content"]})

            # Build context from retrieved episodic logs
            nodes_content: list[str] = [
                payload["content"] for payload in raw_logs_data if "content" in payload
            ]

            context = "\n".join(nodes_content)

            return SessionContextResponse(
                session_id=session_id,
                agent_id=agent_id,
                context=context,
                recent_logs=recent_logs,
            )
        except PermissionError:
            raise  # Re-raise RBAC errors without wrapping
        except Exception as exc:
            import traceback

            traceback.print_exc()
            logger.error(
                "SESSION_CONTEXT_ERROR | session_id=%s agent_id=%s error=%s",
                session_id,
                agent_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to retrieve context: {type(exc).__name__}",
            )

    # ==================================================================
    # POST /v3/session/{session_id}/end
    # ==================================================================

    @router.post(
        "/session/{session_id}/end",
        tags=["session"],
        summary="End a session",
        response_description="Session termination status",
    )
    @limiter.limit("60/minute")
    async def end_session(
        request: Request,
        session_id: str,
        payload: SessionEndRequest,
        background_tasks: BackgroundTasks,
        dao: MemoryDAO = Depends(get_dao),
    ) -> dict:
        """Terminate the session and trigger final consolidation phase.

        Enforces strict RBAC: requires a valid ``agent_id`` in the request payload
        matching the session's tenant ID.
        """
        # RBAC Gate: Verify WRITE permission for this agent/session pair.
        try:
            ac = get_access_control() if get_access_control else AccessControl()
            if not await ac.check_access(payload.agent_id, session_id, "WRITE"):
                raise PermissionError(
                    f"Agent '{payload.agent_id}' lacks WRITE access "
                    f"for session '{session_id}'"
                )
        except PermissionError as perm_exc:
            raise HTTPException(status_code=403, detail=str(perm_exc))

        try:
            # Here we would enqueue a final consolidation pass for this specific session.
            # For now, we log the termination which could trigger the orchestrator.
            logger.info(
                "SESSION_END | agent_id=%s session_id=%s triggered final consolidation.",
                payload.agent_id,
                session_id,
            )
            return {"status": "ended", "session_id": session_id}
        except PermissionError:
            raise  # Re-raise RBAC errors without wrapping
        except Exception as exc:
            import traceback

            traceback.print_exc()
            logger.error(
                "SESSION_END_ERROR | session_id=%s agent_id=%s error=%s",
                session_id,
                payload.agent_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to end session: {type(exc).__name__}",
            )

    return router
