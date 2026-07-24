#!/usr/bin/env python3
# MESA v0.7.0 — Lightweight Dev/Load-Test Server
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
from types import SimpleNamespace
from typing import Any

from dotenv import load_dotenv

# Ensure the project root is on sys.path when running from scripts/
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

load_dotenv()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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
    graph_engine: Any | None = None
    gateway_heartbeat: Any | None = None
    graph_provider: KuzuGraphProvider | None = None
    dao: MemoryDAO | None = None
    access_control: AccessControl | None = None

    # Full mode workers
    consolidation_loop: Any | None = None
    maintenance_worker: Any | None = None
    rem_worker: Any | None = None

    # Control Plane
    client_repo: Any | None = None
    conn_repo: Any | None = None
    settings_repo: Any | None = None
    policy_repo: Any | None = None
    activity_repo: Any | None = None
    approval_repo: Any | None = None


_state = _AppState()


# ---------------------------------------------------------------------------
# Parse CLI args early (needed for --no-auth flag at app creation time)
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_server",
        description="MESA v0.7.0 — Dev/Load-Test Server",
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
    try:
        await initialize_schema(_state.sqlite_engine)
        logger.info("Schema initialized via schemas.py")
    except BaseException as e:
        logger.error(
            f"Error initializing schema: {e.__class__.__name__}: {e}", exc_info=True
        )
        raise

    # --- LanceDB vector engine ---
    _state.vector_engine = VectorEngine(uri="./storage/vector.lance")
    await _state.vector_engine.initialize()
    logger.info("Vector engine initialized: ./storage/vector.lance")

    # --- Mount Control Plane Router ---
    from mesa_storage.control.activity_repo import ActivityRecorder
    from mesa_storage.control.approval_repo import ApprovalRepository
    from mesa_storage.control.client_repo import ClientRepository
    from mesa_storage.control.connection_repo import ConnectionRepository
    from mesa_storage.control.policy_repo import PolicyRepository
    from mesa_storage.control.settings_repo import SettingsRepository

    _state.client_repo = ClientRepository(sqlite_engine=_state.sqlite_engine)
    _state.conn_repo = ConnectionRepository(sqlite_engine=_state.sqlite_engine)
    _state.settings_repo = SettingsRepository(sqlite_engine=_state.sqlite_engine)
    _state.policy_repo = PolicyRepository(sqlite_engine=_state.sqlite_engine)
    _state.activity_repo = ActivityRecorder(sqlite_engine=_state.sqlite_engine)
    _state.approval_repo = ApprovalRepository(sqlite_engine=_state.sqlite_engine)

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

    # --- Mount Control Plane Router ---
    from mesa_api.routers.control.router import create_control_router
    from mesa_api.v4_router import create_v4_router
    from mesa_storage.control.activity_repo import ActivityRecorder
    from mesa_storage.control.approval_repo import ApprovalRepository
    from mesa_storage.control.client_repo import ClientRepository
    from mesa_storage.control.connection_repo import ConnectionRepository
    from mesa_storage.control.policy_repo import PolicyRepository
    from mesa_storage.control.settings_repo import SettingsRepository

    _state.client_repo = ClientRepository(sqlite_engine=_state.sqlite_engine)
    _state.conn_repo = ConnectionRepository(sqlite_engine=_state.sqlite_engine)
    _state.settings_repo = SettingsRepository(sqlite_engine=_state.sqlite_engine)
    _state.policy_repo = PolicyRepository(sqlite_engine=_state.sqlite_engine)
    _state.activity_repo = ActivityRecorder(sqlite_engine=_state.sqlite_engine)
    _state.approval_repo = ApprovalRepository(sqlite_engine=_state.sqlite_engine)

    def get_client_repo():
        return _state.client_repo

    def get_conn_repo():
        return _state.conn_repo

    def get_settings_repo():
        return _state.settings_repo

    def get_policy_repo():
        return _state.policy_repo

    def get_activity_repo():
        return _state.activity_repo

    def get_approval_repo():
        return _state.approval_repo

    control_router = create_control_router(
        get_client_repo=get_client_repo,
        get_conn_repo=get_conn_repo,
        get_settings_repo=get_settings_repo,
        get_policy_repo=get_policy_repo,
        get_activity_repo=get_activity_repo,
        get_approval_repo=get_approval_repo,
    )
    app.include_router(control_router)
    logger.info("Control plane router mounted")

    # --- Mount V4 Router ---
    v4_router = create_v4_router(get_dao=get_dao, get_access_control=get_ac)
    app.include_router(v4_router)
    logger.info("v4 memory router mounted")

    # --- Mount MCP HTTP Gateway ---
    from mesa_mcp.adapter import MesaMCPAdapter
    from mesa_mcp.configuration import MCPSettings
    from mesa_mcp.gateway.auth import GatewayAuth
    from mesa_mcp.gateway.heartbeat import HeartbeatMonitor
    from mesa_mcp.gateway.http_gateway import create_gateway_router
    from mesa_mcp.gateway.middleware import ControlPlaneMiddleware
    from mesa_mcp.http_service import MesaHttpMemoryService
    from mesa_mcp.v4_service import MesaHttpV4Service

    gateway_settings = MCPSettings()
    gateway_auth = GatewayAuth(client_repo=_state.client_repo)
    gateway_heartbeat = HeartbeatMonitor(conn_repo=_state.conn_repo)

    _state.gateway_heartbeat = gateway_heartbeat
    await gateway_heartbeat.start()

    # We need an adapter to process gateway calls locally.
    v4_svc = MesaHttpV4Service(gateway_settings) if gateway_settings.use_v4 else None
    v3_svc = MesaHttpMemoryService(gateway_settings)
    adapter = MesaMCPAdapter(v3_svc, gateway_settings, v4_svc)

    gateway_middleware = ControlPlaneMiddleware(engine=_state.sqlite_engine)

    gateway_router = create_gateway_router(
        adapter=adapter,
        auth=gateway_auth,
        heartbeat=gateway_heartbeat,
        conn_repo=_state.conn_repo,
        middleware=gateway_middleware,
    )
    app.include_router(gateway_router)
    logger.info("HTTP MCP Gateway mounted")

    # --- Mount Dashboard ---
    dashboard_path = os.path.join(_project_root, "mesa_dashboard", "dist")
    if os.path.isdir(dashboard_path):
        # Mount only the assets folder using StaticFiles
        assets_path = os.path.join(dashboard_path, "assets")
        if os.path.isdir(assets_path):
            app.mount(
                "/dashboard/assets",
                StaticFiles(directory=assets_path),
                name="dashboard-assets",
            )

        # Manually serve index.html for any other route under /dashboard to support React SPA
        @app.get("/dashboard/{full_path:path}")
        async def serve_dashboard_spa(full_path: str):
            # If the file exists in the root of dist (e.g. favicon.svg, vite.svg), serve it directly
            file_path = os.path.join(dashboard_path, full_path)
            if full_path and os.path.isfile(file_path):
                return FileResponse(file_path)
            # Otherwise return index.html for client-side routing
            return FileResponse(os.path.join(dashboard_path, "index.html"))

        logger.info("Dashboard mounted at /dashboard")
    else:
        logger.warning(
            f"Dashboard dist folder not found at {dashboard_path}. Build it with 'npm run build'"
        )

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

    if getattr(_state, "gateway_heartbeat", None):
        await _state.gateway_heartbeat.stop()


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="MESA Dev Server",
    version="0.7.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Optional API key middleware (skipped with --no-auth)
# ---------------------------------------------------------------------------

if not _cli_args.no_auth:
    _MESA_API_KEY = os.environ.get("MESA_API_KEY", "")
    _MESA_PRINCIPAL_ID = os.environ.get("MESA_PRINCIPAL_ID", "")
    _MESA_PRINCIPAL_TYPE = os.environ.get("MESA_PRINCIPAL_TYPE", "SERVICE")
    if not _MESA_API_KEY:
        logger.warning(
            "MESA_API_KEY is not set. Use --no-auth to bypass, or set "
            "MESA_API_KEY in your .env file."
        )

    @app.middleware("http")
    async def api_key_middleware(request: Request, call_next):
        # Skip auth for demo and health/metrics/docs endpoints
        if (
            request.url.path.startswith("/demo")
            or request.url.path.startswith("/dashboard")
            or request.url.path.startswith("/control")
            or request.url.path.startswith("/mcp")
            or request.url.path
            in (
                "/health",
                "/metrics",
                "/docs",
                "/openapi.json",
                "/redoc",
            )
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
        if not _MESA_PRINCIPAL_ID:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "detail": "API principal is not configured",
                },
            )
        request.state.principal = SimpleNamespace(
            principal_id=_MESA_PRINCIPAL_ID,
            principal_type=_MESA_PRINCIPAL_TYPE,
            status="active",
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
        "version": "0.7.0",
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
    print("  MESA v0.7.0 Dev Server")
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
