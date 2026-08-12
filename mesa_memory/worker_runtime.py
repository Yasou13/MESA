"""Worker-only durable cold-path consumer with recovery and readiness."""

# ruff: noqa: E402 -- logging must be configured before runtime imports.

from __future__ import annotations

import asyncio
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import structlog

from mesa_memory.observability.logger import setup_logging

setup_logging(role="worker")

from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.config import (
    RuntimeProfile,
    RuntimeProfileConfig,
    RuntimeProfileError,
    config,
    load_explicit_dotenv,
    load_runtime_profile,
)
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.projection_generations import ProjectionGenerationRepository
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

logger = structlog.get_logger("MESA_WorkerRuntime")

_READINESS_NAME = "worker-readiness.json"
_RECOVERY_INTERVAL_SECONDS = 30.0
_DISPATCH_POLL_SECONDS = 1.0
_DISPATCH_LEASE_RENEWAL_SECONDS = 60.0
_WORKER_ID = "worker-runtime"


def _write_readiness(storage_root: Path, payload: dict[str, Any]) -> None:
    target = storage_root / _READINESS_NAME
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    data = {
        **payload,
        "pid": os.getpid(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    directory = os.open(storage_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


async def _recover_once(dao: MemoryDAO | AsyncEngine) -> dict[str, int]:
    """Recover leased work; retain engine-only compatibility for old callers."""
    owns_secondary_stores = isinstance(dao, MemoryDAO)
    recovery_dao = dao if owns_secondary_stores else MemoryDAO(dao, cast(VectorEngine, None))
    result = {
        "raw_log_claims": await recovery_dao.recover_expired_raw_log_claims(),
        "wal_claims": await recovery_dao.recover_expired_lancedb_wal_claims(),
        "session_finalizations": await recovery_dao.recover_expired_session_finalizations(),
    }
    if owns_secondary_stores:
        purges = await recovery_dao.resume_incomplete_purges()
        result["purges"] = sum(outcome == "FINALIZED" for outcome in purges.values())
    return result


async def _consume_dispatches_once(
    dao: MemoryDAO, *, model_processing_enabled: bool
) -> dict[str, int]:
    """Consume bounded dispatch records; only this worker runs cold-path work."""
    claimed = await dao.claim_dispatch_queue(worker_id=_WORKER_ID, limit=1)
    finalized = 0
    retried = 0
    for dispatch in claimed:
        log_id = int(dispatch["payload_reference"])
        agent_id = str(dispatch["agent_id"])
        await _process_dispatch_with_lease(
            dao,
            dispatch,
            log_id=log_id,
            agent_id=agent_id,
            model_processing_enabled=model_processing_enabled,
        )
        raw_log = await dao.get_raw_log(agent_id, log_id)
        status = str(raw_log.get("status", "failed") if raw_log else "failed")
        terminal = status.split(":", 1)[0] in {"processed", "rejected"}
        completed = await dao.complete_dispatch_queue(
            str(dispatch["queue_record_id"]),
            worker_id=_WORKER_ID,
            claim_token=str(dispatch["claim_token"]),
            outcome=status[:120],
            side_effect_verified=terminal,
        )
        finalized += int(completed)
        retried += int(not completed)
    return {"claimed": len(claimed), "finalized": finalized, "retried": retried}


async def _process_dispatch_with_lease(
    dao: MemoryDAO,
    dispatch: dict[str, Any],
    *,
    log_id: int,
    agent_id: str,
    model_processing_enabled: bool,
) -> None:
    """Keep dispatch ownership fenced until its cold-path side effect finishes."""
    processing = asyncio.create_task(
        process_cold_path(
            log_id,
            agent_id,
            dao,
            model_processing_enabled=model_processing_enabled,
        )
    )
    while not processing.done():
        done, _ = await asyncio.wait(
            {processing}, timeout=_DISPATCH_LEASE_RENEWAL_SECONDS
        )
        if not done:
            renewed = await dao.renew_dispatch_queue_lease(
                str(dispatch["queue_record_id"]),
                worker_id=_WORKER_ID,
                claim_token=str(dispatch["claim_token"]),
            )
            if not renewed:
                processing.cancel()
                try:
                    await processing
                except asyncio.CancelledError:
                    pass
                raise RuntimeError("worker dispatch lease ownership was lost")
    await processing


async def _run_worker_owned(runtime: RuntimeProfileConfig) -> None:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)

    engine: AsyncEngine | None = None
    vector_engine: VectorEngine | None = None
    graph_provider: KuzuGraphProvider | None = None
    supervisor: WorkerSupervisor | None = None
    running = False
    try:
        engine = AsyncEngine(str(runtime.storage_root / "mesa.db"), max_connections=2)
        await engine.initialize()
        await initialize_schema(engine)
        projection_paths = await ProjectionGenerationRepository(engine).resolve_active(
            storage_root=runtime.storage_root,
            trusted_root=runtime.storage_root,
        )
        embedding_provider = None
        if runtime.external_provider_enabled:
            embedding_provider = AdapterFactory.get_adapter().aembed
        vector_engine = VectorEngine(
            str(projection_paths.vector_path),
            allow_model_loading=runtime.model_enabled,
            embedding_provider=embedding_provider,
            local_embedding_model=config.local_embedding_model,
        )
        await vector_engine.initialize()
        from mesa_storage import kuzu_setup

        kuzu_setup.initialize_schema_artifact(str(projection_paths.graph_path))
        graph_provider = KuzuGraphProvider(str(projection_paths.graph_path))
        await graph_provider.initialize()
        dao = MemoryDAO(engine, vector_engine, graph_provider=graph_provider)
        await dao.initialize()
        supervisor = WorkerSupervisor(max_restarts=3)
        initial_recovery = await _recover_once(dao)

        async def recovery_loop() -> None:
            while not stopped.is_set():
                dispatch = await _consume_dispatches_once(
                    dao,
                    model_processing_enabled=runtime.model_enabled,
                )
                finalizations = await dao.list_pending_session_finalizations(limit=1)
                for finalization in finalizations:
                    await process_session_finalization(
                        str(finalization["agent_id"]),
                        str(finalization["session_id"]),
                        dao,
                        None,
                    )
                projections = await process_projection_outbox_once(
                    dao, worker_id=_WORKER_ID
                )
                cleanup = await process_artifact_cleanup_once(dao, worker_id=_WORKER_ID)
                try:
                    await asyncio.wait_for(
                        stopped.wait(), timeout=_DISPATCH_POLL_SECONDS
                    )
                except TimeoutError:
                    recovered = await _recover_once(dao)
                    _write_readiness(
                        runtime.storage_root,
                        {
                            "status": "RUNNING",
                            "mode": "durable-cold-path-consumer",
                            "recovered": recovered,
                            "dispatch": dispatch,
                            "finalizations": len(finalizations),
                            "projections": projections["completed"],
                            "cleanup": cleanup["completed"],
                        },
                    )

        await supervisor.start("durable-lease-recovery", recovery_loop)
        await asyncio.sleep(0)
        if supervisor.readiness()["status"] != "healthy":
            raise RuntimeError("worker supervisor failed its startup readiness gate")
        _write_readiness(
            runtime.storage_root,
            {
                "status": "RUNNING",
                "mode": "durable-cold-path-consumer",
                "recovered": initial_recovery,
            },
        )

        running = True
        logger.info("WORKER_RUNTIME_RUNNING", worker_id=_WORKER_ID)
        await stopped.wait()
    finally:
        if supervisor is not None:
            try:
                await supervisor.shutdown()
            except Exception as exc:
                logger.warning(
                    "WORKER_SUPERVISOR_CLOSE_FAILED", error=type(exc).__name__
                )
        if vector_engine is not None:
            try:
                await vector_engine.close()
            except Exception as exc:
                logger.warning("VECTOR_ENGINE_CLOSE_FAILED", error=type(exc).__name__)
        if graph_provider is not None:
            try:
                await graph_provider.close()
            except Exception as exc:
                logger.warning("GRAPH_ENGINE_CLOSE_FAILED", error=type(exc).__name__)
        if engine is not None:
            try:
                await engine.close()
            except Exception as exc:
                logger.warning("SQLITE_ENGINE_CLOSE_FAILED", error=type(exc).__name__)
        remove_signal_handler = getattr(loop, "remove_signal_handler", None)
        if remove_signal_handler is not None:
            for sig in (signal.SIGTERM, signal.SIGINT):
                remove_signal_handler(sig)
        if running:
            _write_readiness(
                runtime.storage_root,
                {"status": "STOPPED", "mode": "durable-cold-path-consumer"},
            )
            logger.info("WORKER_RUNTIME_STOPPED", worker_id=_WORKER_ID)


async def run_worker_only() -> None:
    runtime = load_runtime_profile()
    if (
        runtime.profile is not RuntimeProfile.WORKER_ONLY
        or runtime.api_enabled
        or not runtime.worker_enabled
    ):
        raise RuntimeProfileError("worker runtime requires the worker-only profile")
    if runtime.model_enabled or runtime.external_provider_enabled:
        raise RuntimeProfileError(
            "model-disabled worker runtime refuses model or external provider activation"
        )
    load_explicit_dotenv(runtime)
    runtime.storage_root.mkdir(parents=True, exist_ok=True)
    try:
        writer_lock = StorageWriterLock.acquire(
            runtime.storage_root, owner="worker-only-runtime"
        )
    except StorageWriterLockError as exc:
        raise RuntimeProfileError(
            "single-writer deployment allows only one active writer per storage root"
        ) from exc
    try:
        await _run_worker_owned(runtime)
    finally:
        writer_lock.release()


def main() -> None:
    asyncio.run(run_worker_only())


if __name__ == "__main__":
    main()
