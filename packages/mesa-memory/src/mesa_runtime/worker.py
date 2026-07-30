"""Canonical worker-only durable cold-path consumer with recovery and readiness."""

# ruff: noqa: E402 -- logging must be configured before runtime imports.

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO, cast

import structlog

from mesa_memory.observability.logger import setup_logging

setup_logging(role="worker")

from mesa_memory.config import (
    RuntimeProfile,
    RuntimeProfileError,
    load_explicit_dotenv,
    load_runtime_profile,
)
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_workers.ingestion_worker import process_cold_path
from mesa_workers.supervision import WorkerSupervisor

logger = structlog.get_logger("MESA_WorkerRuntime")

_READINESS_NAME = "worker-readiness.json"
_RECOVERY_INTERVAL_SECONDS = 30.0
_DISPATCH_POLL_SECONDS = 1.0
_DISPATCH_LEASE_RENEWAL_SECONDS = 60.0
_WORKER_ID = "worker-runtime"
_WRITER_LOCK_NAME = ".mesa-single-writer.lock"


def _acquire_writer_lock(storage_root: Path) -> TextIO:
    """Fence a storage root to one active worker process on its host."""
    handle = (storage_root / _WRITER_LOCK_NAME).open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeProfileError(
            "single-writer deployment allows only one active worker per storage root"
        ) from exc
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    os.fsync(handle.fileno())
    return handle


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


async def _recover_once(engine: AsyncEngine) -> dict[str, int]:
    dao = MemoryDAO(engine, cast(VectorEngine, None))
    return {
        "raw_log_claims": await dao.recover_expired_raw_log_claims(),
        "wal_claims": await dao.recover_expired_lancedb_wal_claims(),
        "session_finalizations": await dao.recover_expired_session_finalizations(),
    }


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
    writer_lock = _acquire_writer_lock(runtime.storage_root)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)

    engine = AsyncEngine(str(runtime.storage_root / "mesa.db"), max_connections=2)
    await engine.initialize()
    await initialize_schema(engine)
    vector_engine = VectorEngine(
        str(runtime.storage_root / "vector.lance"),
        allow_model_loading=runtime.model_enabled,
    )
    await vector_engine.initialize()
    dao = MemoryDAO(engine, vector_engine)
    await dao.initialize()
    supervisor = WorkerSupervisor(max_restarts=3)
    initial_recovery = await _recover_once(engine)

    async def recovery_loop() -> None:
        while not stopped.is_set():
            dispatch = await _consume_dispatches_once(
                dao,
                model_processing_enabled=runtime.model_enabled,
            )
            try:
                await asyncio.wait_for(stopped.wait(), timeout=_DISPATCH_POLL_SECONDS)
            except TimeoutError:
                recovered = await _recover_once(engine)
                _write_readiness(
                    runtime.storage_root,
                    {
                        "status": "RUNNING",
                        "mode": "durable-cold-path-consumer",
                        "recovered": recovered,
                        "dispatch": dispatch,
                    },
                )

    await supervisor.start("durable-lease-recovery", recovery_loop)
    await asyncio.sleep(0)
    if supervisor.readiness()["status"] != "healthy":
        await supervisor.shutdown()
        await engine.close()
        raise RuntimeError("worker supervisor failed its startup readiness gate")
    _write_readiness(
        runtime.storage_root,
        {
            "status": "RUNNING",
            "mode": "durable-cold-path-consumer",
            "recovered": initial_recovery,
        },
    )

    logger.info("WORKER_RUNTIME_RUNNING", worker_id=_WORKER_ID)
    await stopped.wait()
    await supervisor.shutdown()
    await vector_engine.close()
    await engine.close()
    fcntl.flock(writer_lock.fileno(), fcntl.LOCK_UN)
    writer_lock.close()
    _write_readiness(
        runtime.storage_root,
        {"status": "STOPPED", "mode": "durable-cold-path-consumer"},
    )
    logger.info("WORKER_RUNTIME_STOPPED", worker_id=_WORKER_ID)


def main() -> None:
    asyncio.run(run_worker_only())


if __name__ == "__main__":
    main()
