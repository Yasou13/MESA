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
from typing import Any

from mesa_memory.observability.logger import setup_logging

setup_logging(role="api")

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from mesa_api.chat_router import create_chat_router
from mesa_api.routers.control.router import create_control_router
from mesa_api.v4_router import create_v4_router
from mesa_mcp.gateway.middleware import ControlPlaneMiddleware
from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.config import (
    RuntimeProfile,
    RuntimeProfileConfig,
    config,
    load_explicit_dotenv,
    load_runtime_profile,
    refresh_config_from_environment,
    warn_removed_env_vars,
)
from mesa_memory.embedding.service import (
    get_embedding_service as _get_embedding_service,
)
from mesa_memory.extraction.service import FactExtractionService
from mesa_memory.observability.http import RequestLoggingMiddleware
from mesa_memory.observability.metrics import (
    update_v4_health_metrics,
)
from mesa_memory.security.api_keys import APIKeyStore
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO
from mesa_storage.projection_generations import (
    ProjectionGenerationRepository,
    ProjectionPaths,
)
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_storage.writer_lock import StorageWriterLock, StorageWriterLockError
from mesa_workers.ingestion_worker import (
    process_cold_path,
    process_session_finalization,
)
from mesa_workers.projection_worker import (
    process_artifact_cleanup_once,
    process_projection_outbox_once,
)
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


async def _require_api_key() -> None:
    """Raise at startup if neither bootstrap nor provisioned key exists.

    Called inside ``lifespan`` so test imports don't crash at module level
    while the production server still refuses to start without a key.
    """
    key_store = getattr(state, "api_key_store", None)
    if not _MESA_API_KEY and (
        key_store is None or not await key_store.has_active_key()
    ):
        raise RuntimeError(
            "MESA_API_KEY environment variable must be set. No local fallback allowed."
        )


async def get_api_key(request: Request, api_key: str = Depends(_API_KEY_HEADER)) -> str:
    """Validate the API key and attach its configured server-side principal."""
    key_store = getattr(state, "api_key_store", None)
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


async def get_openai_compat_api_key(
    request: Request, api_key: str = Depends(_API_KEY_HEADER)
) -> str:
    """Authenticate /v1 OpenAI-compatible calls via Bearer token or X-API-Key (D104)."""
    auth_header = request.headers.get("authorization") or ""
    bearer_key: str | None = None
    if auth_header:
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail="Invalid or missing API Key")
        bearer_key = token.strip()
    if bearer_key and api_key and not secrets.compare_digest(bearer_key, api_key):
        raise HTTPException(status_code=401, detail="Conflicting API key headers")
    resolved = bearer_key or api_key or ""
    return await get_api_key(request, api_key=resolved)


class AppState:
    sqlite_engine: AsyncEngine
    vector_engine: VectorEngine
    dao: MemoryDAO
    fact_extraction_service: FactExtractionService | None
    access_control: AccessControl
    api_key_store: APIKeyStore
    background_tasks: set[asyncio.Task]
    worker_supervisor: WorkerSupervisor
    mcp_control: ControlPlaneMiddleware
    is_ready: bool


state = AppState()

# ---------------------------------------------------------------------------
# Storage path resolution — configurable via MESA_STORAGE_PATH env var
# ---------------------------------------------------------------------------
_STORAGE_BASE: Path | None = None
_SQLITE_PATH: Path | None = None
_VECTOR_PATH: Path | None = None


def _configure_runtime_paths(runtime: RuntimeProfileConfig) -> None:
    global _STORAGE_BASE, _SQLITE_PATH, _VECTOR_PATH
    _STORAGE_BASE = runtime.storage_root
    _SQLITE_PATH = _STORAGE_BASE / "mesa.db"
    _VECTOR_PATH = None


def _configure_projection_paths(paths: ProjectionPaths) -> None:
    global _VECTOR_PATH
    _VECTOR_PATH = paths.vector_path


def _acquire_runtime_writer_lock(
    runtime: RuntimeProfileConfig,
) -> StorageWriterLock | None:
    """Fence the combined runtime before it opens any writable embedded store."""
    if runtime.profile is not RuntimeProfile.COMBINED:
        return None
    try:
        return StorageWriterLock.acquire(runtime.storage_root, owner="combined-runtime")
    except StorageWriterLockError as exc:
        raise RuntimeError(
            "combined runtime could not acquire storage writer ownership"
        ) from exc


async def _consume_combined_durable_work_once(
    dao: MemoryDAO,
    *,
    fact_extraction_service: FactExtractionService | None = None,
    model_processing_enabled: bool = False,
) -> dict[str, int]:
    """Consume bounded durable work in the single storage-owner runtime."""
    worker_id = "combined-runtime"
    claimed = await dao.claim_dispatch_queue(worker_id=worker_id, limit=50)

    async def _handle_one_dispatch(dispatch: dict[str, Any]) -> None:
        log_id = int(dispatch["payload_reference"])
        agent_id = str(dispatch["agent_id"])
        await process_cold_path(
            log_id,
            agent_id,
            dao,
            fact_extraction_service=fact_extraction_service,
            model_processing_enabled=model_processing_enabled,
            retry_on_failure=True,
        )
        raw_log = await dao.get_raw_log(agent_id, log_id)
        status = str(raw_log.get("status", "DEFERRED") if raw_log else "DEFERRED")
        await dao.complete_dispatch_queue(
            str(dispatch["queue_record_id"]),
            worker_id=worker_id,
            claim_token=str(dispatch["claim_token"]),
            outcome=status[:120],
            side_effect_verified=status.split(":", 1)[0] in {"processed", "rejected"},
        )

    if claimed:
        await asyncio.gather(
            *(_handle_one_dispatch(d) for d in claimed), return_exceptions=True
        )

    finalizations = await dao.list_pending_session_finalizations(limit=10)
    for finalization in finalizations:
        await process_session_finalization(
            str(finalization["agent_id"]),
            str(finalization["session_id"]),
            dao,
            fact_extraction_service,
        )
    projections = {"completed": 0}
    cleanup = {"completed": 0}
    if type(dao) is MemoryDAO:
        projections = await process_projection_outbox_once(
            dao, worker_id=worker_id, limit=100
        )
        cleanup = await process_artifact_cleanup_once(
            dao, worker_id=worker_id, limit=100
        )
    return {
        "dispatches": len(claimed),
        "finalizations": len(finalizations),
        "projections": projections["completed"],
        "cleanup": cleanup["completed"],
    }


async def _run_combined_durable_consumer(
    dao: MemoryDAO,
    *,
    fact_extraction_service: FactExtractionService | None = None,
    model_processing_enabled: bool = False,
) -> None:
    """Poll the durable journal without introducing a second storage writer."""
    while True:
        try:
            await _consume_combined_durable_work_once(
                dao,
                fact_extraction_service=fact_extraction_service,
                model_processing_enabled=model_processing_enabled,
            )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.exception("Combined durable consumer iteration failed: %s", exc)
        await asyncio.sleep(0.05)


@asynccontextmanager
async def _runtime_lifespan(app: FastAPI, runtime: RuntimeProfileConfig):
    refresh_config_from_environment()
    state.is_ready = False
    state.background_tasks = set()
    state.worker_supervisor = WorkerSupervisor(max_restarts=3)

    reranker = None
    if config.rerank_enabled:
        from mesa_memory.reranking import CrossEncoderReranker

        reranker = CrossEncoderReranker(config.rerank_model)

    # Initialize asynchronous storage engines
    state.sqlite_engine = AsyncEngine(db_path=str(_SQLITE_PATH))
    await state.sqlite_engine.initialize()

    await initialize_schema(state.sqlite_engine)
    generation_repository = ProjectionGenerationRepository(state.sqlite_engine)
    projection_paths = await generation_repository.resolve_active(
        storage_root=runtime.storage_root,
        trusted_root=runtime.storage_root,
    )
    _configure_projection_paths(projection_paths)
    state.projection_generation_id = projection_paths.generation_id  # type: ignore[attr-defined]

    embedding_service = _get_embedding_service(
        allow_model_loading=runtime.embedding_enabled,
        force_refresh=True,
        detect_dimension=(runtime.embedding_enabled and "MESA_EMBEDDING_DIMENSION" not in os.environ),
    )
    await generation_repository.assert_active_embedding_identity(
        embedding_service.identity().as_dict()
    )
    state.vector_engine = VectorEngine(
        uri=str(_VECTOR_PATH),
        max_workers=config.vector_worker_limit,
        embedding_service=embedding_service,
    )
    await state.vector_engine.initialize()

    # Wire the unified Data Access Object
    state.dao = MemoryDAO(
        sqlite_engine=state.sqlite_engine,
        vector_engine=state.vector_engine,
        canonical_v4_writes_enabled=(runtime.profile is RuntimeProfile.COMBINED),
        embedding_enabled=runtime.embedding_enabled,
        rrf_k=config.rrf_k,
        rrf_weights={
            "vector": config.rrf_vector_weight,
            "bm25": config.rrf_bm25_weight,
            "assertion": config.rrf_assertion_weight,
        },
        search_scope_cache_enabled=config.search_scope_cache,
        legal_rules_enabled=config.legal_rules_enabled,
        reranker=reranker,
        rerank_depth=config.rerank_depth,
    )
    await state.dao.initialize()
    if runtime.profile is RuntimeProfile.COMBINED:
        await state.dao.recover_processing_raw_logs()
        await state.dao.recover_in_flight_projection_and_dispatch()
        if runtime.embedding_enabled:
            await state.dao.requeue_skipped_vector_projections()

    # The MCP control plane shares the API's single SQLite storage
    # owner.  It must not construct or close a second AsyncEngine lifecycle.
    state.mcp_control = ControlPlaneMiddleware(engine=state.sqlite_engine)

    # Initialize RBAC policy engine — MUST complete before port opens
    state.access_control = AccessControl(
        policy_path=str(runtime.storage_root / "rbac_policy.db")
    )
    await state.access_control.initialize()
    logger.info(
        "AccessControl initialised at %s", runtime.storage_root / "rbac_policy.db"
    )
    state.api_key_store = APIKeyStore(str(runtime.storage_root / "rbac_policy.db"))
    await state.api_key_store.initialize()
    await state.api_key_store.bootstrap_legacy_key(
        secret=_MESA_API_KEY,
        principal_id=_MESA_PRINCIPAL_ID,
        principal_type=_MESA_PRINCIPAL_TYPE,
    )
    # Keep existing deployments fail-closed: their bootstrap secret must be
    # configured on first start.  Thereafter issued rotation keys are checked
    # from the hashed registry by ``get_api_key``.
    await _require_api_key()

    # Model/provider and worker startup are explicit profile decisions.
    wal_task = None
    state.fact_extraction_service = None
    if runtime.worker_enabled and runtime.extraction_enabled:
        logger.info("EXTRACTION_ADAPTER_INITIALIZATION_STARTED")
        extraction_adapter = AdapterFactory.get_extraction_adapter()
        state.fact_extraction_service = FactExtractionService(llm=extraction_adapter)

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
            "Runtime profile %s starts API/storage without extraction worker",
            runtime.profile,
        )

    if runtime.profile is RuntimeProfile.COMBINED:
        combined_task = await state.worker_supervisor.start(
            "combined-durable-consumer",
            lambda: _run_combined_durable_consumer(
                state.dao,
                fact_extraction_service=state.fact_extraction_service,
                model_processing_enabled=runtime.extraction_enabled,
            ),
        )
        state.background_tasks.add(combined_task)
        logger.info("COMBINED_DURABLE_CONSUMER_STARTED")

    logger.info("MESA_API_READY")
    state.is_ready = True
    yield
    logger.info("MESA_API_SHUTDOWN")

    # ==================================================================
    # Teardown — cancel all background workers
    # ==================================================================

    # Stop supervised queue workers first so no new claim is accepted during drain.
    await state.worker_supervisor.shutdown()
    delattr(state, "worker_supervisor")

    # Cancel the WAL worker
    if wal_task is not None:
        wal_task.cancel()
        with suppress(asyncio.CancelledError):
            await wal_task

    state.fact_extraction_service = None

    vector_engine = getattr(state, "vector_engine", None)
    if vector_engine:
        await vector_engine.close()
        delattr(state, "vector_engine")
    if state.sqlite_engine:
        await state.sqlite_engine.close()
        delattr(state, "sqlite_engine")


async def _close_runtime_storage_resources() -> None:
    """Best-effort fallback for partial startup and exceptional shutdown."""
    state.is_ready = False
    supervisor = getattr(state, "worker_supervisor", None)
    if supervisor is not None:
        try:
            await supervisor.shutdown()
        except Exception as exc:
            logger.warning("Failed to stop worker supervisor: %s", exc)
        else:
            delattr(state, "worker_supervisor")

    tasks = list(getattr(state, "background_tasks", set()))
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    for attribute in ("vector_engine", "access_control"):
        resource = getattr(state, attribute, None)
        close = getattr(resource, "close", None)
        if close is None:
            continue
        try:
            await close()
        except Exception as exc:
            logger.warning("Failed to close %s: %s", attribute, exc)
        else:
            delattr(state, attribute)

    # APIKeyStore opens short-lived SQLite connections per operation and has
    # no close hook, but the process-global state must not retain a registry
    # from a previous lifespan/storage root.  A stale registry makes the next
    # lifespan authenticate against the wrong deployment database.
    if hasattr(state, "api_key_store"):
        delattr(state, "api_key_store")

    sqlite_engine = getattr(state, "sqlite_engine", None)
    if sqlite_engine is not None:
        try:
            await sqlite_engine.close()
        except Exception as exc:
            logger.warning("Failed to close sqlite_engine: %s", exc)
        else:
            delattr(state, "sqlite_engine")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Configure and fence the storage root before opening any embedded store.
    bootstrap = load_runtime_profile()
    load_explicit_dotenv(bootstrap)
    warn_removed_env_vars()
    refresh_config_from_environment()
    runtime = load_runtime_profile()
    _refresh_auth_config()
    _configure_runtime_paths(runtime)
    state.runtime_profile = runtime  # type: ignore[attr-defined]
    assert _STORAGE_BASE is not None
    _STORAGE_BASE.mkdir(parents=True, exist_ok=True)
    writer_lock = _acquire_runtime_writer_lock(runtime)
    try:
        async with _runtime_lifespan(app, runtime):
            yield
    finally:
        try:
            await _close_runtime_storage_resources()
        finally:
            if writer_lock is not None:
                writer_lock.release()


app = FastAPI(title="MESA API", version=__version__, lifespan=lifespan)


def _canonical_error_code(status_code: int, detail: str) -> str:
    normalized = detail.casefold()
    if status_code == 409 and "session is not active" in normalized:
        return "SESSION_INACTIVE"
    if status_code == 409 and "revision" in normalized and "conflict" in normalized:
        return "REVISION_HEAD_CONFLICT"
    if status_code == 409 and "idempotency" in normalized:
        return "IDEMPOTENCY_CONFLICT"
    return {
        400: "INVALID_ARGUMENT",
        401: "UNAUTHORIZED",
        403: "ACCESS_DENIED",
        404: "NOT_FOUND",
        409: "CONFLICT",
        413: "PAYLOAD_TOO_LARGE",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
        501: "NOT_SUPPORTED",
        503: "BACKEND_UNAVAILABLE",
    }.get(status_code, "HTTP_ERROR")


def _canonical_error_body(status_code: int, detail: str) -> dict[str, object]:
    return {
        "error": _canonical_error_code(status_code, detail),
        "detail": detail,
        "status_code": status_code,
        "retryable": status_code in {408, 429, 502, 503, 504},
    }


@app.exception_handler(HTTPException)
async def canonical_http_exception_response(
    _request: Request, exc: HTTPException
) -> JSONResponse:
    detail = str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=_canonical_error_body(exc.status_code, detail),
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def canonical_validation_exception_response(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    # Exception text includes rejected input and internal route/source details.
    # Keep the existing string contract while identifying the field and remedy.
    detail = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    )
    return JSONResponse(
        status_code=422,
        content=_canonical_error_body(422, detail),
    )


@app.exception_handler(Exception)
async def unhandled_exception_response(request: Request, exc: Exception) -> Response:
    """Preserve FastAPI's generic 500 body while returning the correlation ID."""
    request.state.exception_type = type(exc).__name__
    request_id = getattr(request.state, "request_id", None)
    headers = {"X-Request-ID": request_id} if request_id else None
    return PlainTextResponse("Internal Server Error", status_code=500, headers=headers)


from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from mesa_memory.api.middleware import limiter, rate_limit_exceeded_handler

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.add_middleware(SlowAPIMiddleware)

# type: ignore[no-untyped-def]


@app.middleware("http")
async def add_api_version_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-API-Version"] = __version__
    return response


app.add_middleware(RequestLoggingMiddleware)


def get_dao() -> MemoryDAO:
    """Dependency injection for the MemoryDAO."""
    if not hasattr(state, "dao") or state.dao is None:
        raise HTTPException(status_code=503, detail="Storage not initialized")
    return state.dao  # type: ignore[no-untyped-def]


def get_embedding_service():
    """Dependency injection for the canonical EmbeddingService."""
    return _get_embedding_service()


def get_embedder():
    """Dependency injection for the embedder function (backward compatibility)."""
    runtime = getattr(state, "runtime_profile", None)
    if runtime is not None and not runtime.embedding_enabled:
        return lambda _text: [0.0] * 8
    try:
        return _get_embedding_service().embed_document
    except Exception as exc:
        raise RuntimeError("canonical EmbeddingService is unavailable") from exc


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


from mesa_memory.api.middleware import check_daily_limit

router_dependencies = [Depends(get_api_key), Depends(check_daily_limit)]
v4_router = create_v4_router(
    get_dao=get_dao,
    get_access_control=get_access_control,
    get_extraction_enabled=lambda: bool(
        getattr(getattr(state, "runtime_profile", None), "extraction_enabled", False)
    ),
)
app.include_router(v4_router, dependencies=router_dependencies)
chat_router = create_chat_router(
    get_dao=get_dao,
    get_access_control=get_access_control,
)
app.include_router(
    chat_router,
    dependencies=[Depends(get_openai_compat_api_key), Depends(check_daily_limit)],
)
app.include_router(
    create_control_router(
        lambda: get_mcp_control().client_repo,
        lambda: get_mcp_control().conn_repo,
        lambda: get_mcp_control().settings_repo,
        lambda: get_mcp_control().policy_repo,
        lambda: get_mcp_control().activity_repo,
        lambda: get_mcp_control().approval_repo,
        lambda: get_mcp_control().credential_repo,
        lambda: get_mcp_control().binding_profile_repo,
        get_access_control,
    ),
    dependencies=router_dependencies,
)
# type: ignore[no-untyped-def]


@app.get("/health/init")
async def health_init():
    """Health probe for container orchestration readiness."""
    if not getattr(state, "is_ready", False):
        raise HTTPException(status_code=503, detail="System initializing")
    health = await state.dao.health_check()
    readiness_failures = _canonical_readiness_failures(health)
    if readiness_failures:
        raise HTTPException(
            status_code=503,
            detail="Canonical work backlog exceeded readiness thresholds: "
            + ", ".join(readiness_failures),
        )
    runtime = getattr(state, "runtime_profile", None)
    workers_required = runtime is None or runtime.worker_enabled
    worker_health = state.worker_supervisor.readiness()
    if workers_required and worker_health["status"] != "healthy":
        raise HTTPException(
            status_code=503, detail="Required workers degraded or blocked"
        )
    if (
        health.get("sqlite", {}).get("status") == "healthy"
        and health.get("vector", {}).get("status") == "healthy"
    ):
        return {"status": "ready"}  # type: ignore[no-untyped-def]
    raise HTTPException(status_code=503, detail="Backend services degraded")


def _canonical_readiness_failures(health: dict[str, object]) -> list[str]:
    """Return severe canonical-work conditions that make this process unready."""
    projection = health.get("v4_projection")
    ownership = health.get("v4_ownership")
    projection = projection if isinstance(projection, dict) else {}
    ownership = ownership if isinstance(ownership, dict) else {}
    checks = (
        (
            "projection_backlog",
            int(projection.get("backlog", 0)),
            config.readiness_projection_backlog_max,
        ),
        (
            "projection_dead_letter",
            int(projection.get("dead_letter", 0)),
            config.readiness_projection_dead_letter_max,
        ),
        (
            "projection_stuck",
            int(projection.get("stuck_claims", 0)),
            config.readiness_projection_stuck_max,
        ),
        (
            "cleanup_backlog",
            int(ownership.get("cleanup_backlog", 0)),
            config.readiness_cleanup_backlog_max,
        ),
        (
            "cleanup_blocked",
            int(ownership.get("cleanup_blocked", 0)),
            config.readiness_cleanup_blocked_max,
        ),
        (
            "orphan_registry",
            int(ownership.get("orphan_registry", 0)),
            config.readiness_orphan_registry_max,
        ),
    )
    return [
        f"{name}={actual}>{maximum}"
        for name, actual, maximum in checks
        if actual > maximum
    ]


@app.get("/health", dependencies=[Depends(get_api_key)])
async def health():  # type: ignore[no-untyped-def]
    health = await state.dao.health_check(detailed=False)
    rebuild = health.get("v4_rebuild") or {}
    backend_healthy = all(
        health.get(component, {}).get("status") in {"healthy", "not_initialized"}
        for component in ("sqlite", "vector", "graph")
        if component in health
    )
    rebuild_state = str(rebuild.get("state", "IDLE"))
    rebuild_status = str(rebuild.get("status", "healthy"))
    return {
        "status": (
            "healthy" if backend_healthy and rebuild_status == "healthy" else "degraded"
        ),
        "version": __version__,
        "maintenance": rebuild_status == "maintenance",
        "rebuild_state": rebuild_state,
    }


@app.get("/metrics", dependencies=[Depends(get_api_key)])
async def metrics():
    update_v4_health_metrics(await state.dao.health_check())
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
