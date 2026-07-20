"""WAVE-003 contracts for durable single-owner work claims and WAL replay."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@dataclass
class _Vector:
    rows: set[tuple[str, str]] = field(default_factory=set)

    async def upsert(self, *, node_id, agent_id, embedding, content_hash=None) -> None:
        self.rows.add((agent_id, node_id))

    async def get_existing_node_ids(self, agent_id: str, node_ids: list[str]) -> set[str]:
        return {node_id for node_id in node_ids if (agent_id, node_id) in self.rows}

    async def get_active_node_ids(self, agent_id: str | None = None) -> set[str]:
        return {node_id for scoped_agent, node_id in self.rows if agent_id is None or scoped_agent == agent_id}


async def _make_dao(tmp_path) -> tuple[MemoryDAO, AsyncEngine]:
    sql = AsyncEngine(str(tmp_path / "wave003.db"), max_connections=4)
    await sql.initialize()
    await initialize_schema(sql)
    return MemoryDAO(sqlite_engine=sql, vector_engine=_Vector()), sql


@pytest.mark.asyncio
async def test_raw_log_claim_is_single_owner_and_terminal_transition_is_fenced(tmp_path):
    dao, sql = await _make_dao(tmp_path)
    try:
        log_id = await dao.insert_raw_log(
            "tenant-a", {"agent_id": "tenant-a", "content": "claim contract"}
        )
        claims = await asyncio.gather(
            dao.claim_raw_log("tenant-a", log_id, worker_id="worker-a"),
            dao.claim_raw_log("tenant-a", log_id, worker_id="worker-b"),
        )
        claimed = [claim for claim in claims if claim is not None]
        assert len(claimed) == 1
        claim = claimed[0]
        assert claim["claimed_by"] in {"worker-a", "worker-b"}

        assert not await dao.transition_claimed_raw_log(
            "tenant-a", log_id, worker_id="other-worker", claim_token=claim["claim_token"], status="processed"
        )
        assert await dao.transition_claimed_raw_log(
            "tenant-a", log_id, worker_id=claim["claimed_by"], claim_token=claim["claim_token"], status="processed"
        )
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_expired_claim_and_wal_item_are_replayable_once_then_acknowledged(tmp_path):
    dao, sql = await _make_dao(tmp_path)
    try:
        log_id = await dao.insert_raw_log(
            "tenant-a", {"agent_id": "tenant-a", "content": "replay contract"}
        )
        first = await dao.claim_raw_log("tenant-a", log_id, worker_id="worker-a")
        assert first is not None
        async with sql.transaction() as db:
            await db.execute(
                "UPDATE raw_logs SET lease_expires_at = datetime('now', '-1 second') WHERE id = ?",
                (log_id,),
            )
            wal_id = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO lancedb_wal (id, agent_id, vector, metadata) VALUES (?, ?, ?, ?)",
                (wal_id, "tenant-a", b"\x00\x00\x80?", '{"node_id":"node-1"}'),
            )
            await db.commit()

        replay = await dao.claim_raw_log("tenant-a", log_id, worker_id="worker-b")
        assert replay is not None
        assert replay["claimed_by"] == "worker-b"

        wal_claims = await asyncio.gather(
            dao.claim_lancedb_wal_entries(worker_id="flusher-a", limit=10),
            dao.claim_lancedb_wal_entries(worker_id="flusher-b", limit=10),
        )
        claimed_wal = [entry for batch in wal_claims for entry in batch]
        assert [entry["id"] for entry in claimed_wal] == [wal_id]
        entry = claimed_wal[0]
        assert not await dao.ack_lancedb_wal_entry(
            wal_id, worker_id=entry["claimed_by"], claim_token=entry["claim_token"]
        )
        assert await dao.replay_claimed_lancedb_wal_entry(entry, worker_id=entry["claimed_by"])
        assert await dao.claim_lancedb_wal_entries(worker_id="flusher-c", limit=10) == []
    finally:
        await sql.close()
