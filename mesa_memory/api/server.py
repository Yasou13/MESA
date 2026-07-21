import faulthandler

faulthandler.enable()
import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import kuzu
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from mesa_api.router import create_memory_router
from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.config import (
    RuntimeProfileConfig,
    load_explicit_dotenv,
    load_runtime_profile,
)
from mesa_memory.consolidation.loop import (
    ConsolidationLoop,
)
from mesa_memory.container_health import worker_is_ready
from mesa_memory.observability.logger import setup_logging
from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_memory.observability.tracer import setup_telemetry_tracing
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_workers.entity_consolidation_worker import schedule_consolidation_worker
from mesa_workers.maintenance import MaintenanceWorker
from mesa_workers.maintenance_pagerank import schedule_pagerank_worker
from mesa_workers.rem_cycle import REMCycleWorker
from mesa_workers.supervision import WorkerSupervisor

try:
    __version__ = version("mesa-memory")
except PackageNotFoundError:
    __version__ = "0.0.0"

logger = logging.getLogger("MESA_Server")

# ---------------------------------------------------------------------------
# API Key Authentication
# ---------------------------------------------------------------------------
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
_MESA_API_KEY: str | None
_MESA_PRINCIPAL_ID: str | None
_MESA_PRINCIPAL_TYPE: str
_MESA_PRINCIPAL_STATUS: str


def _refresh_auth_config() -> None:
    """Refresh auth settings after an explicitly allowed dotenv load."""
    global _MESA_API_KEY, _MESA_PRINCIPAL_ID, _MESA_PRINCIPAL_TYPE, _MESA_PRINCIPAL_STATUS
    _MESA_API_KEY = os.environ.get("MESA_API_KEY")
    _MESA_PRINCIPAL_ID = os.environ.get("MESA_PRINCIPAL_ID")
    _MESA_PRINCIPAL_TYPE = os.environ.get("MESA_PRINCIPAL_TYPE", "SERVICE")
    _MESA_PRINCIPAL_STATUS = os.environ.get("MESA_PRINCIPAL_STATUS", "active")


_refresh_auth_config()


@dataclass(frozen=True)
class PrincipalContext:
    """Authenticated server-side identity available to memory routes."""

    principal_id: str
    principal_type: str
    status: str = "active"


def _require_api_key() -> None:
    """Raise at startup if the API key is missing.

    Called inside ``lifespan`` so test imports don't crash at module level
    while the production server still refuses to start without a key.
    """
    if not _MESA_API_KEY:
        raise RuntimeError(
            "MESA_API_KEY environment variable must be set. No local fallback allowed."
        )


async def get_api_key(request: Request, api_key: str = Depends(_API_KEY_HEADER)) -> str:
    """Validate the API key and attach its configured server-side principal."""
    if (
        not api_key
        or not _MESA_API_KEY
        or not secrets.compare_digest(api_key, _MESA_API_KEY)
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    if not _MESA_PRINCIPAL_ID:
        raise HTTPException(status_code=401, detail="API principal is not configured")

    request.state.principal = PrincipalContext(
        principal_id=_MESA_PRINCIPAL_ID,
        principal_type=_MESA_PRINCIPAL_TYPE,
        status=_MESA_PRINCIPAL_STATUS,
    )
    return api_key


class AppState:
    sqlite_engine: AsyncEngine
    vector_engine: VectorEngine
    kuzu_db: kuzu.Database
    graph_provider: KuzuGraphProvider
    dao: MemoryDAO
    obs_layer: ObservabilityLayer
    consolidation_loop: ConsolidationLoop
    access_control: AccessControl
    background_tasks: set[asyncio.Task]
    worker_supervisor: WorkerSupervisor
    is_ready: bool


state = AppState()

# ---------------------------------------------------------------------------
# Storage path resolution — configurable via MESA_STORAGE_PATH env var
# ---------------------------------------------------------------------------
_STORAGE_BASE: Path | None = None
_SQLITE_PATH: Path | None = None
_VECTOR_PATH: Path | None = None
_KUZU_PATH: Path | None = None
_VALENCE_PATH: Path | None = None


def _configure_runtime_paths(runtime: RuntimeProfileConfig) -> None:
    global _STORAGE_BASE, _SQLITE_PATH, _VECTOR_PATH, _KUZU_PATH, _VALENCE_PATH
    _STORAGE_BASE = runtime.storage_root
    _SQLITE_PATH = _STORAGE_BASE / "mesa.db"
    _VECTOR_PATH = _STORAGE_BASE / "vector.lance"
    _KUZU_PATH = _STORAGE_BASE / "kuzu_db"
    _VALENCE_PATH = _STORAGE_BASE / "valence_state.db"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ==================================================================
    # Configure Structured Logging  # type: ignore[no-untyped-def]
    setup_logging()

    runtime = load_runtime_profile()
    load_explicit_dotenv(runtime)
    _refresh_auth_config()
    _configure_runtime_paths(runtime)
    state.runtime_profile = runtime  # type: ignore[attr-defined]

    # Initialize LLM telemetry (Langfuse/Langsmith) only after profile validation.  # type: ignore[attr-defined]
    setup_telemetry_tracing()

    # Fail-fast: refuse to start without a valid API key
    _require_api_key()

    # Ensure the validated base storage directory exists before any DB initialization
    assert _STORAGE_BASE is not None
    _STORAGE_BASE.mkdir(parents=True, exist_ok=True)

    state.is_ready = False

    state.obs_layer = ObservabilityLayer()
    state.background_tasks = set()
    state.worker_supervisor = WorkerSupervisor(max_restarts=3)

    # Initialize asynchronous storage engines
    state.sqlite_engine = AsyncEngine(db_path=str(_SQLITE_PATH))
    await state.sqlite_engine.initialize()

    # Schema DDL — single source of truth (B-1 fix)
    await initialize_schema(state.sqlite_engine)

    state.vector_engine = VectorEngine(
        uri=str(_VECTOR_PATH), allow_model_loading=runtime.model_enabled
    )
    await state.vector_engine.initialize()

    # Initialize KùzuDB embedded graph database (disk-backed)
    # NOTE: Only the Database handle is created here. kuzu.Connection
    # instances must be created per-thread to avoid file-lock contention.
    if _KUZU_PATH is not None:
        _KUZU_PATH.parent.mkdir(parents=True, exist_ok=True)
    # type: ignore[union-attr]
    logger.info("KUZU_SCHEMA_INITIALIZATION_STARTED")
    from mesa_storage import kuzu_setup

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, kuzu_setup.initialize_schema, str(_KUZU_PATH))
    logger.info("KUZU_SCHEMA_INITIALIZATION_COMPLETED")

    state.kuzu_db = await loop.run_in_executor(None, kuzu.Database, str(_KUZU_PATH))
    logger.info("KùzuDB initialised at %s", _KUZU_PATH)
    logger.info("KUZU_DATABASE_OPENED")

    # Initialize the async-safe KuzuGraphProvider for edge operations
    state.graph_provider = KuzuGraphProvider(db_path=str(_KUZU_PATH))
    await state.graph_provider.initialize()

    # Wire the unified Data Access Object
    state.dao = MemoryDAO(
        sqlite_engine=state.sqlite_engine,
        vector_engine=state.vector_engine,
        graph_provider=state.graph_provider,
    )
    await state.dao.initialize()

    # Initialize RBAC policy engine — MUST complete before port opens
    state.access_control = AccessControl(
        policy_path=str(_STORAGE_BASE / "rbac_policy.db")
    )
    await state.access_control.initialize()
    logger.info("AccessControl initialised at %s", _STORAGE_BASE / "rbac_policy.db")

    # Model/provider and worker startup are explicit profile decisions.
    pagerank_task = None
    wal_task = None
    maintenance_worker = None
    rem_worker = None
    state.consolidation_loop = None  # type: ignore[assignment]
    if runtime.worker_enabled and runtime.model_enabled:  # type: ignore[assignment]
        logger.info("CONSOLIDATION_ADAPTER_INITIALIZATION_STARTED")
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
        consolidation_loop_task = await state.worker_supervisor.start(
            "consolidation-loop", state.consolidation_loop.start
        )
        state.background_tasks.add(consolidation_loop_task)
        logger.info("CONSOLIDATION_LOOP_STARTED")

        # ------------------------------------------------------------------
        # Valence state restoration (prevents threshold amnesia on restart)
        # ------------------------------------------------------------------
        _valence_db = str(_VALENCE_PATH)
        try:
            if Path(_valence_db).exists():
                _router = getattr(state.consolidation_loop, "router", None)
                _valence = getattr(_router, "valence_motor", None)
                if _valence is not None:
                    await _valence.load_state(_valence_db)
                    logger.info("Valence state restored from %s", _valence_db)
                else:
                    logger.debug(
                        "VALENCE_LOAD_SKIP | ValenceMotor not found on "
                        "consolidation_loop.router — skipping state restore"
                    )
            else:
                logger.debug(
                    "VALENCE_LOAD_SKIP | %s does not exist — cold start",
                    _valence_db,
                )
        except (FileNotFoundError, OSError) as fs_exc:
            logger.warning(
                "VALENCE_LOAD_FAILED | filesystem error=%s — starting with defaults",
                fs_exc,
            )
        except Exception as exc:
            logger.warning(
                "VALENCE_LOAD_FAILED | error=%s — starting with defaults",
                exc,
            )

        # ------------------------------------------------------------------
        # Background workers: PageRank, Maintenance, REM Cycle
        # ------------------------------------------------------------------
        pagerank_task = None
        try:
            logger.info("PAGERANK_WORKER_STARTING")
            pagerank_task = await state.worker_supervisor.start(
                "pagerank", lambda: schedule_pagerank_worker(dao=state.dao)
            )
            logger.info("PAGERANK_WORKER_STARTED")
            state.background_tasks.add(pagerank_task)
            logger.info("PageRank worker scheduled successfully.")
        except Exception as exc:
            logger.error("Failed to schedule PageRank worker: %s", exc)

        consolidation_task = None
        try:
            logger.info("ENTITY_CONSOLIDATION_WORKER_STARTING")
            consolidation_adapter = AdapterFactory.get_adapter()
            consolidation_task = await state.worker_supervisor.start(
                "entity-consolidation",
                lambda: schedule_consolidation_worker(
                    dao=state.dao, llm_adapter=consolidation_adapter
                ),
            )
            logger.info("ENTITY_CONSOLIDATION_WORKER_STARTED")
            state.background_tasks.add(consolidation_task)
            logger.info("Consolidation worker scheduled successfully.")
        except Exception as exc:
            logger.error("Failed to schedule Consolidation worker: %s", exc)

        # ------------------------------------------------------------------
        # Background workers: Tier-3 Deferred and DLQ
        # ------------------------------------------------------------------
        try:
            from mesa_memory.consolidation.loop import (
                start_dlq_worker,
                start_tier3_deferred_worker,
            )

            tier3_task = await state.worker_supervisor.start(
                "tier3-deferred",
                lambda: start_tier3_deferred_worker(
                    dao=state.dao,
                    consolidation_loop=state.consolidation_loop,
                    sleep_interval=15,
                    batch_size=20,
                ),
            )
            state.background_tasks.add(tier3_task)
            logger.info("Tier-3 Deferred worker scheduled successfully.")

            dlq_task = await state.worker_supervisor.start(
                "dlq",
                lambda: start_dlq_worker(
                    dao=state.dao,
                    consolidation_loop=state.consolidation_loop,
                    sleep_interval=60,
                    batch_size=10,
                ),
            )
            state.background_tasks.add(dlq_task)
            logger.info("DLQ re-processing worker scheduled successfully.")
        except Exception as exc:
            logger.error("Failed to schedule Tier-3/DLQ workers: %s", exc)

        vacuum_hours_env = os.environ.get("MESA_VACUUM_HOURS", "3")
        try:
            schedule_hours = [
                int(h.strip()) for h in vacuum_hours_env.split(",") if h.strip()
            ]
        except ValueError:
            schedule_hours = [3]

        maintenance_worker = MaintenanceWorker(
            sqlite_engine=state.sqlite_engine,
            vector_engine=state.vector_engine,
            schedule_hours=schedule_hours,
        )
        try:
            logger.info("MAINTENANCE_WORKER_STARTING")
            await maintenance_worker.start()
            if maintenance_worker._task:
                state.background_tasks.add(maintenance_worker._task)
            logger.info("MAINTENANCE_WORKER_STARTED")
            logger.info("MaintenanceWorker started successfully.")
        except Exception as exc:
            logger.error("Failed to start MaintenanceWorker: %s", exc)

        rem_llm_a = AdapterFactory.get_adapter()
        rem_llm_b = AdapterFactory.get_adapter()
        rem_worker = REMCycleWorker(
            dao=state.dao,
            llm_a=rem_llm_a,
            llm_b=rem_llm_b,
        )
        try:
            await rem_worker.start()
            if rem_worker._task:
                state.background_tasks.add(rem_worker._task)
            logger.info("REMCycleWorker started successfully.")
        except Exception as exc:
            logger.error("Failed to start REMCycleWorker: %s", exc)

        # ------------------------------------------------------------------
        # Background worker: SQLite WAL Checkpointer
        # ------------------------------------------------------------------
        async def wal_checkpoint_worker():
            while True:  # type: ignore[no-untyped-def]
                try:
                    await asyncio.sleep(300)  # Checkpoint every 5 minutes
                    if hasattr(state, "sqlite_engine") and state.sqlite_engine:
                        async with state.sqlite_engine.connection() as db:
                            await db.execute("PRAGMA wal_checkpoint(PASSIVE);")
                        logger.info("WAL_CHECKPOINT | PASSIVE checkpoint executed.")
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning("WAL_CHECKPOINT_FAILED | error=%s", exc)

        wal_task = await state.worker_supervisor.start(
            "wal-checkpoint", wal_checkpoint_worker, required=False
        )
        state.background_tasks.add(wal_task)
        logger.info("WAL Checkpoint worker started successfully.")

    else:
        logger.info(
            "Runtime profile %s starts API/storage without workers", runtime.profile
        )

    logger.info("MESA_API_READY")
    state.is_ready = True
    yield
    logger.info("MESA_API_SHUTDOWN")

    # ==================================================================
    # Teardown — cancel all background workers
    # ==================================================================

    # Stop supervised queue workers first so no new claim is accepted during drain.
    await state.worker_supervisor.shutdown()

    # Stop REMCycleWorker gracefully
    if rem_worker is not None:
        try:
            await rem_worker.stop()
        except Exception as exc:
            logger.warning("Failed to stop REMCycleWorker: %s", exc)
        if rem_worker._task is not None:
            rem_worker._task.cancel()
            with suppress(asyncio.CancelledError):
                await rem_worker._task

    # Stop MaintenanceWorker gracefully
    if maintenance_worker is not None:
        try:
            await maintenance_worker.stop()
        except Exception as exc:
            logger.warning("Failed to stop MaintenanceWorker: %s", exc)
        if maintenance_worker._task is not None:
            maintenance_worker._task.cancel()
            with suppress(asyncio.CancelledError):
                await maintenance_worker._task

    # Cancel the PageRank worker
    if pagerank_task is not None:
        pagerank_task.cancel()
        with suppress(asyncio.CancelledError):
            await pagerank_task
    # Cancel the WAL worker
    if wal_task is not None:
        wal_task.cancel()
        with suppress(asyncio.CancelledError):
            await wal_task

    # Stop the consolidation loop before flushing state
    if hasattr(state, "consolidation_loop") and state.consolidation_loop:
        await state.consolidation_loop.stop()

    # v0.6.1 FIX: Persist valence cognitive state to prevent amnesia.
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
                _valence_save_path = str(_VALENCE_PATH)
                await _valence.save_state(_valence_save_path)
                logger.info("Valence state persisted to %s", _valence_save_path)
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

from slowapi.errors import RateLimitExceeded

from mesa_memory.api.middleware import limiter, rate_limit_exceeded_handler

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # type: ignore[arg-type]
from mesa_memory.observability.metrics import PROM_HTTP_REQUESTS

# type: ignore[no-untyped-def]


@app.middleware("http")
async def add_api_version_header(request: Request, call_next):
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-API-Version"] = __version__
        return response
    finally:
        # Exclude metrics endpoint from skewing results
        if request.url.path != "/metrics":
            PROM_HTTP_REQUESTS.labels(
                method=request.method, endpoint=request.url.path, status=status_code
            ).inc()


def get_dao() -> MemoryDAO:
    """Dependency injection for the MemoryDAO."""
    if not hasattr(state, "dao") or state.dao is None:
        raise HTTPException(status_code=503, detail="Storage not initialized")
    return state.dao  # type: ignore[no-untyped-def]


def get_embedder():
    """Dependency injection for the embedder function."""
    runtime = getattr(state, "runtime_profile", None)
    if runtime is not None and not runtime.model_enabled:
        return lambda _text: [0.0] * 8
    return AdapterFactory.get_adapter().embed


def get_consolidation_loop() -> ConsolidationLoop | None:
    """Dependency injection for the ConsolidationLoop.

    Returns ``None`` before the lifespan has initialised the loop,
    which safely disables Tier-3 consensus during startup.
    """
    return getattr(state, "consolidation_loop", None)


def get_access_control() -> AccessControl:
    """Dependency injection for the AccessControl singleton.

    Returns the instance initialised during lifespan startup.
    Raises 503 if called before lifespan completes.
    """
    ac = getattr(state, "access_control", None)  # type: ignore[no-any-return]
    if ac is None:
        raise HTTPException(status_code=503, detail="AccessControl not initialized")
    return ac


from mesa_memory.api.middleware import check_daily_limit

# Setup v3 API Router utilizing Dependency Injection
# Requires depends at the router level for auth and rate limits
router_dependencies = [Depends(get_api_key), Depends(check_daily_limit)]
memory_router = create_memory_router(
    get_dao=get_dao,
    get_embedder=get_embedder,
    get_consolidation_loop=get_consolidation_loop,
    get_access_control=get_access_control,
    prefix="/v3/memory",
)
# We can't attach dependencies to the include_router directly if the router already defines some,
# but it's simpler to inject them directly on include_router
app.include_router(memory_router, dependencies=router_dependencies)
# type: ignore[no-untyped-def]


@app.get("/health/init")
async def health_init():
    """Health probe for container orchestration readiness."""
    if not getattr(state, "is_ready", False):
        raise HTTPException(status_code=503, detail="System initializing")
    health = await state.dao.health_check()
    runtime = getattr(state, "runtime_profile", None)
    workers_required = runtime is None or runtime.worker_enabled
    worker_health = state.worker_supervisor.readiness()
    if workers_required and worker_health["status"] != "healthy":
        raise HTTPException(
            status_code=503, detail="Required workers degraded or blocked"
        )
    if (
        runtime is not None
        and getattr(runtime, "require_worker_readiness", False)
        and not worker_is_ready(runtime.storage_root)
    ):
        raise HTTPException(status_code=503, detail="External worker is not ready")
    if (
        health.get("sqlite", {}).get("status") == "healthy"
        and health.get("vector", {}).get("status") == "healthy"
    ):
        if health.get("graph", {}).get("status") in ("healthy", "not_initialized"):
            return {"status": "ready"}  # type: ignore[no-untyped-def]
    raise HTTPException(status_code=503, detail="Backend services degraded")


@app.get("/v3/health", dependencies=[Depends(get_api_key)])
async def health_v3():  # type: ignore[no-untyped-def]
    return await state.dao.health_check()


@app.get("/health", dependencies=[Depends(get_api_key)])
async def health():  # type: ignore[no-untyped-def]
    return await state.dao.health_check()


@app.get("/metrics", dependencies=[Depends(get_api_key)])
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
