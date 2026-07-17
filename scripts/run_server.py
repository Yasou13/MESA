#!/usr/bin/env python3
# MESA v0.6.0 — Lightweight Dev/Load-Test Server
# Boots the MESA FastAPI app on port 8000 for local development and
# load testing without requiring the full ML stack (REBEL, transformers).
#
# Usage:
#   python scripts/run_server.py                      # default :8000
#   python scripts/run_server.py --port 8001          # custom port
#   python scripts/run_server.py --no-auth            # disable API key check
#
# This script wires up:
#   - AsyncEngine (SQLite WAL)  → ./storage/mesa.db
#   - VectorEngine (LanceDB)   → ./storage/vector.lance
#   - MemoryDAO
#   - v3 memory router (/v3/memory/insert, /search, /purge)
#   - /health endpoint
#
# Cold-path processing runs inside FastAPI's BackgroundTasks pool.
# No separate worker process is needed.

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv

# Ensure the project root is on sys.path when running from scripts/
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

load_dotenv()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from mesa_api.router import create_memory_router  # noqa: E402
from mesa_memory.security.rbac import AccessControl  # noqa: E402
from mesa_storage.dao import MemoryDAO  # noqa: E402
from mesa_storage.kuzu_provider import KuzuGraphProvider  # noqa: E402
from mesa_storage.schemas import initialize_schema  # noqa: E402
from mesa_storage.sqlite_engine import AsyncEngine  # noqa: E402
from mesa_storage.vector_engine import VectorEngine  # noqa: E402

logger = logging.getLogger("MESA_DevServer")


# ---------------------------------------------------------------------------
# Application state container
# ---------------------------------------------------------------------------


class _AppState:
    sqlite_engine: AsyncEngine | None = None
    vector_engine: VectorEngine | None = None
    graph_provider: KuzuGraphProvider | None = None
    dao: MemoryDAO | None = None
    access_control: AccessControl | None = None

    # Full mode workers
    consolidation_loop: Any | None = None
    maintenance_worker: Any | None = None
    rem_worker: Any | None = None


_state = _AppState()


# ---------------------------------------------------------------------------
# Parse CLI args early (needed for --no-auth flag at app creation time)
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_server",
        description="MESA v0.6.0 — Dev/Load-Test Server",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=int(os.environ.get("MESA_PORT", "8000")),
        help="Port to bind (default: 8000)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Disable X-API-Key authentication (for load testing)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload (development mode)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Start ConsolidationLoop, MaintenanceWorker, and REMCycleWorker",
    )
    # When launched via `uvicorn`, sys.argv may contain unexpected args.
    # parse_known_args tolerates that gracefully.
    args, _ = parser.parse_known_args()
    return args


_cli_args = _parse_args()


# ---------------------------------------------------------------------------
# Lifespan: boot storage engines
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize async storage engines on startup, tear down on shutdown."""
    os.makedirs("./storage", exist_ok=True)

    # --- SQLite WAL engine ---
    _state.sqlite_engine = AsyncEngine(db_path="./storage/mesa.db")
    await _state.sqlite_engine.initialize()
    logger.info("SQLite engine initialized: ./storage/mesa.db")

    # --- Schema DDL (single source of truth) ---
    await initialize_schema(_state.sqlite_engine)
    logger.info("Schema initialized via schemas.py")

    # --- LanceDB vector engine ---
    _state.vector_engine = VectorEngine(uri="./storage/vector.lance")
    await _state.vector_engine.initialize()
    logger.info("Vector engine initialized: ./storage/vector.lance")

    # --- KuzuDB graph engine ---
    from mesa_storage import kuzu_setup

    kuzu_setup.initialize_schema("./storage/kuzu_db")
    import asyncio

    import kuzu

    loop = asyncio.get_running_loop()
    _ = await loop.run_in_executor(None, kuzu.Database, "./storage/kuzu_db")
    _state.graph_provider = KuzuGraphProvider(db_path="./storage/kuzu_db")
    await _state.graph_provider.initialize()
    logger.info("KùzuDB graph engine initialized: ./storage/kuzu_db")

    # --- MemoryDAO ---
    _state.dao = MemoryDAO(
        sqlite_engine=_state.sqlite_engine,
        vector_engine=_state.vector_engine,
        graph_provider=_state.graph_provider,
    )
    await _state.dao.initialize()
    logger.info("MemoryDAO wired")

    def get_dao() -> MemoryDAO:
        if _state.dao is None:
            raise RuntimeError("DAO not initialized")
        return _state.dao

    # --- AccessControl ---
    _state.access_control = AccessControl(policy_path="./storage/rbac_policy.db")
    await _state.access_control.initialize()
    logger.info("AccessControl initialized")

    def get_ac() -> AccessControl:
        assert _state.access_control is not None
        return _state.access_control

    # --- Background Workers (--full) ---
    if _cli_args.full:
        import asyncio

        from mesa_memory.adapter.ollama import OllamaAdapter
        from mesa_memory.consolidation.loop import ConsolidationLoop
        from mesa_memory.observability.metrics import ObservabilityLayer
        from mesa_workers.maintenance import MaintenanceWorker
        from mesa_workers.rem_cycle import REMCycleWorker

        _state.consolidation_loop = ConsolidationLoop(
            dao=_state.dao,
            embedder=OllamaAdapter(model="nomic-embed-text"),
            llm_a=OllamaAdapter(model="mistral"),
            llm_b=OllamaAdapter(model="mistral"),
            obs_layer=ObservabilityLayer(),
        )
        asyncio.create_task(_state.consolidation_loop.start())
        logger.info("ConsolidationLoop started (Tier-3 consensus enabled)")

        _state.maintenance_worker = MaintenanceWorker(
            sqlite_engine=_state.sqlite_engine,
            vector_engine=_state.vector_engine,
            retention_hours=24,
        )
        asyncio.create_task(_state.maintenance_worker.start())
        logger.info("MaintenanceWorker started")

        _state.rem_worker = REMCycleWorker(
            dao=_state.dao,
            llm_a=OllamaAdapter(model="mistral"),
            llm_b=OllamaAdapter(model="mistral"),
            poll_interval_seconds=600,
        )
        asyncio.create_task(_state.rem_worker.start())
        logger.info("REMCycleWorker started")

    def get_cl():
        return _state.consolidation_loop

    # --- Mount v3 memory router ---
    router = create_memory_router(
        get_dao=get_dao,
        get_access_control=get_ac,
        get_consolidation_loop=get_cl,
        prefix="/v3/memory",
    )
    app.include_router(router)
    logger.info("v3 memory router mounted")

    yield

    # --- Shutdown ---
    if _state.consolidation_loop:
        await _state.consolidation_loop.stop()
    if _state.maintenance_worker:
        await _state.maintenance_worker.stop()
    if _state.rem_worker:
        await _state.rem_worker.stop()

    if _state.sqlite_engine:
        await _state.sqlite_engine.close()
        logger.info("SQLite engine closed")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="MESA Dev Server",
    version="0.6.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Optional API key middleware (skipped with --no-auth)
# ---------------------------------------------------------------------------

if not _cli_args.no_auth:
    _MESA_API_KEY = os.environ.get("MESA_API_KEY", "")
    if not _MESA_API_KEY:
        logger.warning(
            "MESA_API_KEY is not set. Use --no-auth to bypass, or set "
            "MESA_API_KEY in your .env file."
        )

    @app.middleware("http")
    async def api_key_middleware(request: Request, call_next):
        # Skip auth for demo and health/metrics/docs endpoints
        if request.url.path.startswith("/demo") or request.url.path in (
            "/health",
            "/metrics",
            "/docs",
            "/openapi.json",
            "/redoc",
        ):
            return await call_next(request)
        api_key = request.headers.get("X-API-Key", "")
        if not secrets.compare_digest(
            api_key.encode("utf-8"), _MESA_API_KEY.encode("utf-8")
        ):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "detail": "Invalid or missing API Key",
                },
            )
        return await call_next(request)


# ---------------------------------------------------------------------------
# Utility endpoints
# ---------------------------------------------------------------------------


@app.get("/metrics")
async def metrics():
    from fastapi import Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health():
    """Basic health check."""
    return {
        "status": "healthy",
        "sqlite": "ok" if _state.sqlite_engine else "not_initialized",
        "vector": "ok" if _state.vector_engine else "not_initialized",
        "version": "0.4.0-dev",
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    auth_status = "DISABLED (--no-auth)" if _cli_args.no_auth else "ENABLED"
    print(f"\n{'=' * 60}")
    print("  MESA v0.6.0 Dev Server")
    print(f"  Bind:    {_cli_args.host}:{_cli_args.port}")
    print(f"  Auth:    {auth_status}")
    print("  Storage: ./storage/mesa.db + ./storage/vector.lance")
    print(f"{'=' * 60}\n")

    uvicorn.run(
        "scripts.run_server:app",
        host=_cli_args.host,
        port=_cli_args.port,
        reload=_cli_args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
