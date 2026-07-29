from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory import worker_runtime
from mesa_memory.config import RuntimeProfile, RuntimeProfileConfig
from mesa_memory.container_health import worker_is_ready
from mesa_memory.worker_runtime import _recover_once
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_model_disabled_worker_recovers_only_expired_durable_claims(
    tmp_path: Path,
) -> None:
    engine = AsyncEngine(str(tmp_path / "mesa.db"), max_connections=2)
    await engine.initialize()
    await initialize_schema(engine)
    async with engine.transaction() as connection:
        await connection.execute(
            "INSERT INTO session_finalization_journal "
            "(finalization_id,agent_id,session_id,idempotency_key,state,attempt_count,retry_limit,"
            "claim_token,claimed_by,lease_expires_at) "
            "VALUES ('f1','agent-a','session-a','end:agent-a:session-a','CLAIMED',1,3,'token','old-worker',datetime('now','-1 second'))"
        )
        await connection.commit()
    recovered = await _recover_once(engine)
    assert recovered == {
        "raw_log_claims": 0,
        "wal_claims": 0,
        "session_finalizations": 1,
    }
    async with engine.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT state,claim_token,claimed_by FROM session_finalization_journal WHERE finalization_id='f1'"
            )
        ).fetchone()
    assert tuple(row) == ("RETRY_PENDING", None, None)
    await engine.close()


@pytest.mark.asyncio
async def test_worker_runtime_initializes_and_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The in-process lifecycle performs the same cleanup as the worker binary."""
    runtime = RuntimeProfileConfig(
        profile=RuntimeProfile.WORKER_ONLY,
        storage_root=tmp_path,
        load_dotenv=False,
        dotenv_path=None,
        model_enabled=False,
        external_provider_enabled=False,
        api_enabled=False,
        worker_enabled=True,
        require_worker_readiness=False,
    )
    engine = SimpleNamespace(initialize=AsyncMock(), close=AsyncMock())
    vector_engine = SimpleNamespace(initialize=AsyncMock(), close=AsyncMock())
    dao = SimpleNamespace(initialize=AsyncMock())
    supervisor = SimpleNamespace(
        start=AsyncMock(),
        readiness=MagicMock(return_value={"status": "healthy"}),
        shutdown=AsyncMock(),
    )
    writer_lock = SimpleNamespace(fileno=MagicMock(return_value=42), close=MagicMock())
    readiness = MagicMock()

    class AlreadyStopped:
        def is_set(self) -> bool:
            return True

        def set(self) -> None:
            return None

        async def wait(self) -> None:
            return None

    monkeypatch.setattr(worker_runtime, "load_runtime_profile", lambda: runtime)
    monkeypatch.setattr(worker_runtime, "load_explicit_dotenv", MagicMock())
    monkeypatch.setattr(worker_runtime, "_acquire_writer_lock", lambda _: writer_lock)
    monkeypatch.setattr(worker_runtime, "AsyncEngine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(
        worker_runtime, "initialize_schema", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        worker_runtime, "VectorEngine", lambda *_args, **_kwargs: vector_engine
    )
    monkeypatch.setattr(worker_runtime, "MemoryDAO", lambda *_args, **_kwargs: dao)
    monkeypatch.setattr(worker_runtime, "WorkerSupervisor", lambda **_kwargs: supervisor)
    monkeypatch.setattr(
        worker_runtime, "_recover_once", AsyncMock(return_value={"raw_log_claims": 0})
    )
    monkeypatch.setattr(worker_runtime, "_write_readiness", readiness)
    monkeypatch.setattr(worker_runtime.asyncio, "Event", AlreadyStopped)
    monkeypatch.setattr(
        worker_runtime.asyncio,
        "get_running_loop",
        lambda: SimpleNamespace(add_signal_handler=MagicMock()),
    )
    monkeypatch.setattr(worker_runtime.fcntl, "flock", MagicMock())

    await worker_runtime.run_worker_only()

    supervisor.start.assert_awaited_once()
    supervisor.shutdown.assert_awaited_once()
    engine.close.assert_awaited_once()
    vector_engine.close.assert_awaited_once()
    writer_lock.close.assert_called_once()
    assert [call.args[1]["status"] for call in readiness.call_args_list] == [
        "RUNNING",
        "STOPPED",
    ]


def test_worker_process_start_health_and_graceful_stop(tmp_path: Path) -> None:
    storage = tmp_path / "worker"
    env = {
        **os.environ,
        "MESA_RUNTIME_PROFILE": "worker-only",
        "MESA_STORAGE_ROOT": str(storage),
        "MESA_LOAD_DOTENV": "false",
        "MESA_MODEL_ENABLED": "false",
        "MESA_EXTERNAL_PROVIDER_ENABLED": "false",
    }
    for name in ("MESA_DOTENV_PATH", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        env.pop(name, None)
    process = subprocess.Popen(
        [sys.executable, "-m", "mesa_memory.worker_runtime"],
        cwd=Path(__file__).parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        readiness = storage / "worker-readiness.json"
        while time.monotonic() < deadline and not worker_is_ready(storage):
            if process.poll() is not None:
                break
            time.sleep(0.1)
        assert process.poll() is None
        assert worker_is_ready(storage)
        assert (
            json.loads(readiness.read_text(encoding="utf-8"))["mode"]
            == "durable-cold-path-consumer"
        )
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 0
        assert json.loads(readiness.read_text(encoding="utf-8"))["status"] == "STOPPED"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
