"""Model-disabled worker-only runtime with durable lease recovery and readiness."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

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
from mesa_workers.supervision import WorkerSupervisor

_READINESS_NAME = "worker-readiness.json"
_RECOVERY_INTERVAL_SECONDS = 30.0


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
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)

    engine = AsyncEngine(str(runtime.storage_root / "mesa.db"), max_connections=2)
    await engine.initialize()
    await initialize_schema(engine)
    supervisor = WorkerSupervisor(max_restarts=3)
    initial_recovery = await _recover_once(engine)

    async def recovery_loop() -> None:
        while not stopped.is_set():
            try:
                await asyncio.wait_for(
                    stopped.wait(), timeout=_RECOVERY_INTERVAL_SECONDS
                )
            except TimeoutError:
                recovered = await _recover_once(engine)
                _write_readiness(
                    runtime.storage_root,
                    {
                        "status": "RUNNING",
                        "mode": "model-disabled-recovery",
                        "recovered": recovered,
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
            "mode": "model-disabled-recovery",
            "recovered": initial_recovery,
        },
    )

    print("WORKER_RUNTIME=RUNNING", flush=True)
    await stopped.wait()
    await supervisor.shutdown()
    await engine.close()
    _write_readiness(
        runtime.storage_root, {"status": "STOPPED", "mode": "model-disabled-recovery"}
    )
    print("WORKER_RUNTIME=STOPPED", flush=True)


def main() -> None:
    asyncio.run(run_worker_only())


if __name__ == "__main__":
    main()
