import asyncio
import logging
import os
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import kuzu
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from mesa_api.router import create_memory_router
from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.consolidation.loop import (
    ConsolidationLoop,
)
from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

try:
    __version__ = version("mesa-memory")
except PackageNotFoundError:
    __version__ = "0.0.0"

logger = logging.getLogger("MESA_Server")

# ---------------------------------------------------------------------------
# API Key Authentication
# ---------------------------------------------------------------------------
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
_MESA_API_KEY = os.environ.get("MESA_API_KEY")

if not _MESA_API_KEY:
    raise RuntimeError(
        "MESA_API_KEY environment variable must be set. No local fallback allowed."
    )


async def get_api_key(api_key: str = Depends(_API_KEY_HEADER)) -> str:
    """Validate the incoming API key against the server-side secret."""
    if not api_key or api_key != _MESA_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    return api_key


class AppState:
    sqlite_engine: AsyncEngine
    vector_engine: VectorEngine
    kuzu_db: kuzu.Database
    graph_provider: KuzuGraphProvider
    dao: MemoryDAO
    obs_layer: ObservabilityLayer
    consolidation_loop: ConsolidationLoop


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ==================================================================
    state.obs_layer = ObservabilityLayer()

    # Initialize asynchronous storage engines
    state.sqlite_engine = AsyncEngine(db_path="./storage/mesa.db")
    await state.sqlite_engine.initialize()

    # Schema DDL — single source of truth (B-1 fix)
    await initialize_schema(state.sqlite_engine)

    state.vector_engine = VectorEngine(uri="./storage/vector.lance")
    await state.vector_engine.initialize()

    # Initialize KùzuDB embedded graph database (disk-backed)
    # NOTE: Only the Database handle is created here. kuzu.Connection
    # instances must be created per-thread to avoid file-lock contention.
    _kuzu_path = Path("./storage/kuzu_db")
    _kuzu_path.parent.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    state.kuzu_db = await loop.run_in_executor(None, kuzu.Database, str(_kuzu_path))
    logger.info("KùzuDB initialised at %s", _kuzu_path)

    # Initialize the async-safe KuzuGraphProvider for edge operations
    state.graph_provider = KuzuGraphProvider(db_path=str(_kuzu_path))
    await state.graph_provider.initialize()

    # Wire the unified Data Access Object
    state.dao = MemoryDAO(
        sqlite_engine=state.sqlite_engine,
        vector_engine=state.vector_engine,
        graph_provider=state.graph_provider,
    )
    await state.dao.initialize()

    # Wire the Consolidation Loop directly to the DAO
    llm_a = AdapterFactory.get_adapter()
    llm_b = AdapterFactory.get_adapter()
    state.consolidation_loop = ConsolidationLoop(
        dao=state.dao,
        embedder=AdapterFactory.get_adapter(),
        llm_a=llm_a,
        llm_b=llm_b,
        obs_layer=state.obs_layer,
    )
    asyncio.create_task(state.consolidation_loop.start())

    yield

    # ==================================================================
    # Shutdown
    # ==================================================================
    # Stop the consolidation loop before flushing state
    if hasattr(state, "consolidation_loop") and state.consolidation_loop:
        await state.consolidation_loop.stop()

    # v0.4.1 FIX: Persist valence cognitive state to prevent amnesia.
    # Without this save, the EWMAD threshold and memory count are lost
    # on every restart, causing threshold regression.
    try:
        from mesa_memory.valence.core import ValenceMotor

        if hasattr(state, "consolidation_loop") and state.consolidation_loop:
            # Walk the consolidation → router → validator chain to find
            # any ValenceMotor instance that may hold live state.
            _router = getattr(state.consolidation_loop, "router", None)
            _valence = getattr(_router, "valence_motor", None)
            if _valence and isinstance(_valence, ValenceMotor):
                await _valence.save_state("./storage/valence_state.db")
                logger.info("Valence state persisted to ./storage/valence_state.db")
    except Exception as exc:
        logger.warning("Failed to persist valence state on shutdown: %s", exc)

    # Close KuzuGraphProvider — releases its per-instance connection
    if hasattr(state, "graph_provider") and state.graph_provider:
        try:
            await state.graph_provider.close()
            logger.info("KuzuGraphProvider closed successfully.")
        except Exception as exc:
            logger.warning("Failed to close KuzuGraphProvider: %s", exc)

    # Close KùzuDB — releases the OS file lock on the database directory
    if hasattr(state, "kuzu_db") and state.kuzu_db:
        try:
            state.kuzu_db.close()
            logger.info("KùzuDB closed successfully.")
        except Exception as exc:
            logger.warning("Failed to close KùzuDB: %s", exc)

    if state.sqlite_engine:
        await state.sqlite_engine.close()


app = FastAPI(title="MESA API", version=__version__, lifespan=lifespan)


def get_dao() -> MemoryDAO:
    """Dependency injection for the MemoryDAO."""
    if not hasattr(state, "dao") or state.dao is None:
        raise HTTPException(status_code=503, detail="Storage not initialized")
    return state.dao


def get_embedder():
    """Dependency injection for the embedder function."""
    return AdapterFactory.get_adapter().embed


# Setup v3 API Router utilizing Dependency Injection
# Requires depends at the router level for auth
router_dependencies = [Depends(get_api_key)]
memory_router = create_memory_router(
    get_dao=get_dao,
    get_embedder=get_embedder,
    prefix="/v3/memory",
)
# We can't attach dependencies to the include_router directly if the router already defines some,
# but it's simpler to inject them directly on include_router
app.include_router(memory_router, dependencies=router_dependencies)


@app.get("/health")
async def health():
    return await state.dao.health_check()


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
