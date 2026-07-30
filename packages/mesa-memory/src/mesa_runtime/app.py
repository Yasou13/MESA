"""Canonical MESA runtime composition root and FastAPI application factory."""

# ruff: noqa: E402 -- logging is configured before runtime implementation imports.

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

from mesa_memory.observability.logger import setup_logging

setup_logging(role="api")

import kuzu
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from mesa_api.router import create_memory_router
from mesa_api.routers.control.router import create_control_router
from mesa_api.v4_router import create_v4_router
from mesa_mcp.gateway.middleware import ControlPlaneMiddleware
from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.api.middleware import (
    check_daily_limit,
    limiter,
    rate_limit_exceeded_handler,
)
from mesa_memory.config import (
    RuntimeProfile,
    RuntimeProfileConfig,
    load_explicit_dotenv,
    load_runtime_profile,
)
from mesa_memory.consolidation.loop import (
    ConsolidationLoop,
)
from mesa_memory.container_health import worker_is_ready
from mesa_memory.observability.http import RequestLoggingMiddleware
from mesa_memory.observability.metrics import (
    ObservabilityLayer,
    update_v4_health_metrics,
)
from mesa_memory.observability.tracer import setup_telemetry_tracing
from mesa_memory.ports import ProjectionStore, supports_capability
from mesa_memory.security.api_keys import APIKeyStore
from mesa_memory.security.rbac import AccessControl
from mesa_runtime.dashboard import install_dashboard
from mesa_runtime.demo import create_demo_router
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.modules import (
    IngestionQueue as IngestionQueueModule,
)
from mesa_storage.modules import (
    LegacyMemoryStore as LegacyMemoryStoreModule,
)
from mesa_storage.modules import (
    MutationLedger as MutationLedgerModule,
)
from mesa_storage.modules import (
    ProjectionStore as ProjectionStoreModule,
)
from mesa_storage.modules import (
    PurgeCoordinator as PurgeCoordinatorModule,
)
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_workers.entity_consolidation_worker import schedule_consolidation_worker
from mesa_workers.ingestion_worker import (
    process_cold_path,
    process_session_finalization,
)
from mesa_workers.maintenance import MaintenanceWorker
from mesa_workers.maintenance_pagerank import schedule_pagerank_worker
from mesa_workers.projection_worker import (
    process_artifact_cleanup_once,
    process_projection_outbox_once,
)
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
    global \
        _MESA_API_KEY, \
        _MESA_PRINCIPAL_ID, \
        _MESA_PRINCIPAL_TYPE, \
        _MESA_PRINCIPAL_STATUS
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


async def _require_api_key(container: "RuntimeContainer | None" = None) -> None:
    """Raise at startup if neither bootstrap nor provisioned key exists.

    Called inside ``lifespan`` so test imports don't crash at module level
    while the production server still refuses to start without a key.
    """
    active = state if container is None else container
    runtime = getattr(active, "runtime_profile", None)
    if runtime is not None and runtime.allow_unauthenticated:
        return
    key_store = getattr(active, "api_key_store", None)
    if not _MESA_API_KEY and (
        key_store is None or not await key_store.has_active_key()
    ):
        raise RuntimeError(
            "MESA_API_KEY environment variable must be set. No local fallback allowed."
        )


async def get_api_key(request: Request, api_key: str = Depends(_API_KEY_HEADER)) -> str:
    """Validate the API key and attach its configured server-side principal."""
    active = getattr(request.app.state, "container", state)
    runtime = getattr(active, "runtime_profile", None)
    if runtime is not None and runtime.allow_unauthenticated:
        request.state.principal = PrincipalContext(
            principal_id="local-development",
            principal_type="USER",
        )
        return ""
    key_store = getattr(active, "api_key_store", None)
    if key_store is not None:
        verified = await key_store.verify(api_key)
        if verified is None:
            raise HTTPException(status_code=401, detail="Invalid or missing API Key")
        request.state.principal = PrincipalContext(
            principal_id=verified.principal_id,
            principal_type=verified.principal_type,
            status=verified.status,
        )
        return api_key or ""
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


@dataclass
class RuntimeContainer:
    """Per-application ownership boundary for all concrete runtime components."""

    runtime_profile: RuntimeProfileConfig | None = None
    sqlite_engine: AsyncEngine | None = None
    vector_engine: VectorEngine | None = None
    kuzu_db: kuzu.Database | None = None
    graph_provider: KuzuGraphProvider | None = None
    dao: MemoryDAO | None = None
    mutation_ledger: MutationLedgerModule | None = None
    projection_store: ProjectionStoreModule | None = None
    ingestion_queue: IngestionQueueModule | None = None
    legacy_memory_store: LegacyMemoryStoreModule | None = None
    purge_coordinator: PurgeCoordinatorModule | None = None
    obs_layer: ObservabilityLayer | None = None
    consolidation_loop: ConsolidationLoop | None = None
    access_control: AccessControl | None = None
    api_key_store: APIKeyStore | None = None
    background_tasks: set[asyncio.Task] | None = None
    worker_supervisor: WorkerSupervisor | None = None
    mcp_control: ControlPlaneMiddleware | None = None
    is_ready: bool = False


# Compatibility-only state for callers importing the pre-0.8 module globals.
# Applications produced by ``create_app`` own independent containers.
AppState = RuntimeContainer
state = RuntimeContainer()

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


async def _consume_combined_durable_work_once(
    dao: MemoryDAO,
    *,
    consolidation_loop: ConsolidationLoop | None,
    model_processing_enabled: bool,
) -> dict[str, int]:
    """Consume bounded durable work in the single storage-owner runtime."""
    worker_id = "combined-runtime"
    claimed = await dao.claim_dispatch_queue(worker_id=worker_id, limit=1)
    for dispatch in claimed:
        log_id = int(dispatch["payload_reference"])
        agent_id = str(dispatch["agent_id"])
        processing = asyncio.create_task(
            process_cold_path(
                log_id,
                agent_id,
                dao,
                consolidation_loop=consolidation_loop,
                model_processing_enabled=model_processing_enabled,
                require_tier3_validation=model_processing_enabled,
                retry_on_failure=True,
            )
        )
        while not processing.done():
            try:
                await asyncio.wait_for(asyncio.shield(processing), timeout=60)
            except TimeoutError:
                renewed = await dao.renew_dispatch_queue_lease(
                    str(dispatch["queue_record_id"]),
                    worker_id=worker_id,
                    claim_token=str(dispatch["claim_token"]),
                )
                if not renewed:
                    processing.cancel()
                    with suppress(asyncio.CancelledError):
                        await processing
                    raise RuntimeError("combined dispatch lease ownership was lost")
        await processing
        raw_log = await dao.get_raw_log(agent_id, log_id)
        status = str(raw_log.get("status", "DEFERRED") if raw_log else "DEFERRED")
        await dao.complete_dispatch_queue(
            str(dispatch["queue_record_id"]),
            worker_id=worker_id,
            claim_token=str(dispatch["claim_token"]),
            outcome=status[:120],
            side_effect_verified=status.split(":", 1)[0] in {"processed", "rejected"},
        )
    finalizations = await dao.list_pending_session_finalizations(limit=1)
    for finalization in finalizations:
        await process_session_finalization(
            str(finalization["agent_id"]),
            str(finalization["session_id"]),
            dao,
            consolidation_loop,
        )
    projections = {"completed": 0}
    cleanup = {"completed": 0}
    if supports_capability(dao, ProjectionStore):
        projections = await process_projection_outbox_once(dao, worker_id=worker_id)
        cleanup = await process_artifact_cleanup_once(dao, worker_id=worker_id)
    return {
        "dispatches": len(claimed),
        "finalizations": len(finalizations),
        "projections": projections["completed"],
        "cleanup": cleanup["completed"],
    }


async def _run_combined_durable_consumer(
    dao: MemoryDAO,
    *,
    consolidation_loop: ConsolidationLoop | None,
    model_processing_enabled: bool,
) -> None:
    """Poll the durable journal without introducing a second storage writer."""
    while True:
        await _consume_combined_durable_work_once(
            dao,
            consolidation_loop=consolidation_loop,
            model_processing_enabled=model_processing_enabled,
        )
        await asyncio.sleep(0.25)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ==================================================================
    # Configure Structured Logging  # type: ignore[no-untyped-def]
    container: RuntimeContainer = app.state.container
    # Keep the implementation below compact while ensuring every assignment is
    # scoped to this application rather than the compatibility module global.
    state = container
    runtime = getattr(app.state, "runtime_settings", None) or load_runtime_profile()
    load_explicit_dotenv(runtime)
    _refresh_auth_config()
    _configure_runtime_paths(runtime)
    state.runtime_profile = runtime  # type: ignore[attr-defined]

    # Initialize LLM telemetry (Langfuse/Langsmith) only after profile validation.  # type: ignore[attr-defined]
    setup_telemetry_tracing()

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

    embedding_provider = None
    if runtime.external_provider_enabled:
        # The V4 retrieval lane must embed queries with the same configured
        # external model used by projection.  Falling back to an unavailable
        # local model creates a dimension mismatch and turns recall into 500.
        embedding_provider = AdapterFactory.get_adapter().aembed
    state.vector_engine = VectorEngine(
        uri=str(_VECTOR_PATH),
        allow_model_loading=runtime.model_enabled,
        embedding_provider=embedding_provider,
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
    dao = MemoryDAO(
        sqlite_engine=state.sqlite_engine,
        vector_engine=state.vector_engine,
        graph_provider=state.graph_provider,
    )
    state.dao = dao
    await dao.initialize()
    state.mutation_ledger = MutationLedgerModule(dao)
    state.projection_store = ProjectionStoreModule(dao)
    state.ingestion_queue = IngestionQueueModule(dao)
    state.legacy_memory_store = LegacyMemoryStoreModule(dao)
    state.purge_coordinator = PurgeCoordinatorModule(dao)

    # The dashboard control plane shares the API's single SQLite storage
    # owner.  It must not construct or close a second AsyncEngine lifecycle.
    state.mcp_control = ControlPlaneMiddleware(engine=state.sqlite_engine)

    # Initialize RBAC policy engine — MUST complete before port opens
    state.access_control = AccessControl(
        policy_path=str(_STORAGE_BASE / "rbac_policy.db")
    )
    await state.access_control.initialize()
    logger.info("AccessControl initialised at %s", _STORAGE_BASE / "rbac_policy.db")
    state.api_key_store = APIKeyStore(str(_STORAGE_BASE / "rbac_policy.db"))
    await state.api_key_store.initialize()
    await state.api_key_store.bootstrap_legacy_key(
        secret=_MESA_API_KEY,
        principal_id=_MESA_PRINCIPAL_ID,
        principal_type=_MESA_PRINCIPAL_TYPE,
    )
    # Keep existing deployments fail-closed: their bootstrap secret must be
    # configured on first start.  Thereafter issued rotation keys are checked
    # from the hashed registry by ``get_api_key``.
    await _require_api_key(container)

    # Model/provider and worker startup are explicit profile decisions.
    pagerank_task = None
    wal_task = None
    maintenance_worker = None
    rem_worker = None
    consolidation_loop: ConsolidationLoop | None = None
    state.consolidation_loop = consolidation_loop
    if runtime.worker_enabled and runtime.model_enabled:  # type: ignore[assignment]
        logger.info("CONSOLIDATION_ADAPTER_INITIALIZATION_STARTED")
        # Wire the Consolidation Loop directly to the DAO
        llm_a, llm_b = AdapterFactory.get_tier3_adapters()
        consolidation_loop = ConsolidationLoop(
            dao=dao,
            embedder=AdapterFactory.get_adapter(),
            llm_a=llm_a,
            llm_b=llm_b,
            obs_layer=state.obs_layer,
        )
        state.consolidation_loop = consolidation_loop
        consolidation_loop_task = await state.worker_supervisor.start(
            "consolidation-loop", consolidation_loop.start
        )
        state.background_tasks.add(consolidation_loop_task)
        logger.info("CONSOLIDATION_LOOP_STARTED")

        # ------------------------------------------------------------------
        # Valence state restoration (prevents threshold amnesia on restart)
        # ------------------------------------------------------------------
        _valence_db = str(_VALENCE_PATH)
        try:
            if Path(_valence_db).exists():
                _router = getattr(consolidation_loop, "router", None)
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
                "pagerank", lambda: schedule_pagerank_worker(dao=dao)
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
                    dao=dao, llm_adapter=consolidation_adapter
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
                    dao=dao,
                    consolidation_loop=consolidation_loop,
                    sleep_interval=15,
                    batch_size=20,
                ),
            )
            state.background_tasks.add(tier3_task)
            logger.info("Tier-3 Deferred worker scheduled successfully.")

            dlq_task = await state.worker_supervisor.start(
                "dlq",
                lambda: start_dlq_worker(
                    dao=dao,
                    consolidation_loop=consolidation_loop,
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

        rem_llm_a, rem_llm_b = AdapterFactory.get_tier3_adapters()
        rem_worker = REMCycleWorker(
            dao=dao,
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

    if runtime.profile is RuntimeProfile.COMBINED:
        combined_task = await state.worker_supervisor.start(
            "combined-durable-consumer",
            lambda: _run_combined_durable_consumer(
                dao,
                consolidation_loop=consolidation_loop,
                model_processing_enabled=runtime.model_enabled,
            ),
        )
        state.background_tasks.add(combined_task)
        logger.info("COMBINED_DURABLE_CONSUMER_STARTED")

    logger.info("MESA_API_READY")
    state.is_ready = True
    app.state.dao = state.dao
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

    # v0.7.1 FIX: Persist valence cognitive state to prevent amnesia.
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


async def unhandled_exception_response(request: Request, exc: Exception) -> Response:
    """Preserve FastAPI's generic 500 body while returning the correlation ID."""
    request.state.exception_type = type(exc).__name__
    request_id = getattr(request.state, "request_id", None)
    headers = {"X-Request-ID": request_id} if request_id else None
    return PlainTextResponse("Internal Server Error", status_code=500, headers=headers)


async def add_api_version_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-API-Version"] = __version__
    return response


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


def get_mcp_control() -> ControlPlaneMiddleware:
    control = getattr(state, "mcp_control", None)
    if control is None:
        raise HTTPException(status_code=503, detail="MCP control plane not initialized")
    return control


def get_runtime_settings() -> RuntimeProfileConfig | None:
    """Return validated settings after startup, or an app-factory override."""
    return getattr(state, "runtime_profile", None)


async def health_init():
    """Health probe for container orchestration readiness."""
    return await _health_init(state)


async def _health_init(container: RuntimeContainer):
    if not getattr(container, "is_ready", False):
        raise HTTPException(status_code=503, detail="System initializing")
    assert container.dao is not None
    assert container.worker_supervisor is not None
    health = await container.dao.health_check()
    runtime = getattr(container, "runtime_profile", None)
    workers_required = runtime is None or runtime.worker_enabled
    worker_health = container.worker_supervisor.readiness()
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


async def health_v3():  # type: ignore[no-untyped-def]
    return await _health_v3(state)


async def _health_v3(container: RuntimeContainer):  # type: ignore[no-untyped-def]
    if container.dao is None:
        raise HTTPException(status_code=503, detail="Storage not initialized")
    health = await container.dao.health_check()
    return {
        "status": "healthy"
        if all(
            component.get("status") in {"healthy", "not_initialized"}
            for component in health.values()
            if isinstance(component, dict)
        )
        else "degraded"
    }


async def health():  # type: ignore[no-untyped-def]
    return await health_v3()


async def metrics():
    return await _metrics(state)


async def _metrics(container: RuntimeContainer):
    if container.dao is None:
        raise HTTPException(status_code=503, detail="Storage not initialized")
    update_v4_health_metrics(await container.dao.health_check())
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def create_app(
    settings: RuntimeProfileConfig | None = None,
    *,
    _container: RuntimeContainer | None = None,
) -> FastAPI:
    """Build the canonical MESA application.

    ``settings`` is primarily useful for embedded deployments and tests.  When
    omitted, startup reads the validated runtime profile from the environment.
    Concrete storage, worker, API, MCP, and dashboard implementations are
    composed only in this module.
    """
    container = _container or RuntimeContainer(runtime_profile=settings)
    if settings is not None:
        container.runtime_profile = settings
    application = FastAPI(title="MESA API", version=__version__, lifespan=lifespan)
    application.state.container = container
    application.state.runtime_settings = settings
    application.state.limiter = limiter
    application.add_exception_handler(Exception, unhandled_exception_response)
    application.add_exception_handler(
        RateLimitExceeded, rate_limit_exceeded_handler  # type: ignore[arg-type]
    )
    application.add_middleware(SlowAPIMiddleware)
    application.middleware("http")(add_api_version_header)
    application.add_middleware(RequestLoggingMiddleware)

    def container_dao() -> MemoryDAO:
        if container.dao is None:
            raise HTTPException(status_code=503, detail="Storage not initialized")
        return container.dao

    def container_embedder():
        runtime = container.runtime_profile
        if runtime is not None and not runtime.model_enabled:
            return lambda _text: [0.0] * 8
        return AdapterFactory.get_adapter().embed

    def container_access_control() -> AccessControl:
        if container.access_control is None:
            raise HTTPException(
                status_code=503, detail="AccessControl not initialized"
            )
        return container.access_control

    def container_mcp_control() -> ControlPlaneMiddleware:
        if container.mcp_control is None:
            raise HTTPException(
                status_code=503, detail="MCP control plane not initialized"
            )
        return container.mcp_control

    async def container_health_init():
        return await _health_init(container)

    async def container_health_v3():
        return await _health_v3(container)

    async def container_metrics():
        return await _metrics(container)

    router_dependencies = [Depends(get_api_key), Depends(check_daily_limit)]
    application.include_router(
        create_memory_router(
            get_dao=container_dao,
            get_embedder=container_embedder,
            get_consolidation_loop=lambda: container.consolidation_loop,
            get_access_control=container_access_control,
            prefix="/v3/memory",
        ),
        dependencies=router_dependencies,
    )
    application.include_router(
        create_v4_router(
            get_dao=container_dao,
            get_access_control=container_access_control,
        ),
        dependencies=router_dependencies,
    )
    application.include_router(
        create_control_router(
            lambda: container_mcp_control().client_repo,
            lambda: container_mcp_control().conn_repo,
            lambda: container_mcp_control().settings_repo,
            lambda: container_mcp_control().policy_repo,
            lambda: container_mcp_control().activity_repo,
            lambda: container_mcp_control().approval_repo,
            lambda: container_mcp_control().credential_repo,
            lambda: container_mcp_control().binding_profile_repo,
            container_access_control,
        ),
        dependencies=router_dependencies,
    )
    application.include_router(
        create_demo_router(
            get_dao=container_dao,
            get_settings=lambda: container.runtime_profile or settings,
            auth_dependency=get_api_key,
        )
    )
    install_dashboard(
        application,
        settings_provider=lambda: container.runtime_profile or settings,
    )
    application.add_api_route("/health/init", container_health_init, methods=["GET"])
    application.add_api_route(
        "/v3/health",
        container_health_v3,
        methods=["GET"],
        dependencies=[Depends(get_api_key)],
    )
    application.add_api_route(
        "/health",
        container_health_v3,
        methods=["GET"],
        dependencies=[Depends(get_api_key)],
    )
    application.add_api_route(
        "/metrics",
        container_metrics,
        methods=["GET"],
        dependencies=[Depends(get_api_key)],
    )
    return application


app = create_app(_container=state)
