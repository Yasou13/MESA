import os
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

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
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

try:
    __version__ = version("mesa-memory")
except PackageNotFoundError:
    __version__ = "0.0.0"

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

    state.vector_engine = VectorEngine(uri="./storage/vector.lance")
    await state.vector_engine.initialize()

    # Wire the unified Data Access Object
    state.dao = MemoryDAO(
        sqlite_engine=state.sqlite_engine, vector_engine=state.vector_engine
    )

    # Wire the Consolidation Loop to the DAO (v0.3.1 P0 Hotfix)
    # ConsolidationLoop now accepts MemoryDAO directly — no StorageFacade.
    # Uncomment and configure LLM adapters for production:
    # llm_a = AdapterFactory.get_adapter("llm_a")
    # llm_b = AdapterFactory.get_adapter("llm_b")
    # state.consolidation_loop = ConsolidationLoop(
    #     dao=state.dao,
    #     embedder=AdapterFactory.get_adapter(),
    #     llm_a=llm_a,
    #     llm_b=llm_b,
    #     obs_layer=state.obs_layer,
    # )
    # asyncio.create_task(state.consolidation_loop.start())

    yield

    # ==================================================================
    # Shutdown
    # ==================================================================
    if state.sqlite_engine:
        await state.sqlite_engine.close()


app = FastAPI(title="MESA API", version=__version__, lifespan=lifespan)

# Setup v3 API Router directly utilizing the MemoryDAO
# Requires depends at the router level for auth
router_dependencies = [Depends(get_api_key)]
memory_router = create_memory_router(
    dao=state.dao,
    embedder=AdapterFactory.get_adapter().embed,
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
