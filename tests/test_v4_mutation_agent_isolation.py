"""Mutation transitions require the owning agent even for a known mutation ID."""

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_mutation_state_cannot_be_changed_by_another_agent(tmp_path):
    sql = AsyncEngine(str(tmp_path / "isolation.sqlite"))
    await sql.initialize()
    await initialize_schema(sql)
    dao = MemoryDAO(sql, None)
    record = MemoryCandidate.from_raw_log(
        raw_log_id=1,
        tenant_id="tenant",
        agent_id="owner",
        session_id="session",
        content_payload="A fact",
    ).as_consolidation_record()
    try:
        await dao.record_mutation(record, raw_log_id=1)
        assert (
            await dao.set_mutation_state(
                "other-agent", record["mutation_id"], "REJECTED"
            )
            is False
        )
        mutation = await dao.get_projection_mutation(record["mutation_id"])
        assert mutation["state"] == "RECEIVED"
        assert (
            await dao.set_mutation_state("owner", record["mutation_id"], "REJECTED")
            is True
        )
    finally:
        await sql.close()
