from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

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
            == "model-disabled-recovery"
        )
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 0
        assert json.loads(readiness.read_text(encoding="utf-8"))["status"] == "STOPPED"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
