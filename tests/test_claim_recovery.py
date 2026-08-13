import asyncio

import pytest

from mesa_memory.config import MesaConfig, load_runtime_profile
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_claim_recovery_after_worker_crash(tmp_path):
    db_path = tmp_path / "test_claim_recovery.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=None, graph_provider=None)
    runtime = load_runtime_profile({
        "MESA_RUNTIME_PROFILE": "combined",
        "MESA_STORAGE_ROOT": str(tmp_path),
    })
    config = MesaConfig(runtime=runtime)
    policy = config.queue_admission_policy

    # 1. Admit a log
    admit_res = await dao.admit_raw_log("agent_crash", {"content": "fact to process"}, policy=policy)
    log_id = admit_res["log_id"]

    # 2. Worker 1 claims log with a short 1-second lease
    claimed = await dao.claim_raw_log("agent_crash", log_id, worker_id="worker_1", lease_seconds=1)
    assert claimed is not None
    assert claimed["id"] == log_id
    assert claimed["attempt_count"] == 1

    # 3. Simulate worker 1 crashing (wait > 1s for lease to expire)
    await asyncio.sleep(1.2)

    # 4. Run recovery
    recovered_count = await dao.recover_expired_raw_log_claims()
    assert recovered_count >= 1, "Expired claim must be recovered"

    # 5. Worker 2 claims the recovered log with attempt_count incremented
    claimed_2 = await dao.claim_raw_log("agent_crash", log_id, worker_id="worker_2", lease_seconds=60)
    assert claimed_2 is not None
    assert claimed_2["id"] == log_id
    assert claimed_2["attempt_count"] == 2

    await engine.close()
