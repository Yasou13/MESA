"""WAVE-003-V durable downstream fence/reconciliation contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


class _Vector:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}
        self.fail = False

    async def upsert(self, **record):
        if self.fail:
            raise RuntimeError("synthetic-vector-failure")
        self.rows[(record["agent_id"], record["node_id"])] = dict(record)

    async def bulk_upsert(self, records):
        for record in records:
            await self.upsert(**record)

    async def get_existing_node_ids(self, agent_id, node_ids):
        return {node_id for node_id in node_ids if (agent_id, node_id) in self.rows}


class _Graph:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}
        self.fail = False

    async def insert_node(self, *, node_id, name, agent_id):
        if self.fail:
            raise RuntimeError("synthetic-graph-failure")
        self.rows[(agent_id, node_id)] = {"name": name}

    async def has_node(self, *, node_id, agent_id):
        return (agent_id, node_id) in self.rows


async def _dao(root: Path):
    engine = AsyncEngine(str(root / "canonical.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    return MemoryDAO(engine, _Vector(), _Graph()), engine


@pytest.mark.asyncio
async def test_downstream_failure_keeps_canonical_row_open_and_stale_fence_cannot_finalize(
    tmp_path: Path,
):
    dao, engine = await _dao(tmp_path)
    try:
        async with engine.transaction() as db:
            await db.execute(
                "INSERT INTO lancedb_wal (id, agent_id, vector, metadata) VALUES (?, ?, ?, ?)",
                (
                    "mutation-1",
                    "tenant-a",
                    b"\x00\x00\x80?",
                    json.dumps(
                        {
                            "node_id": "node-1",
                            "entity_name": "Entity",
                            "graph_required": True,
                        }
                    ),
                ),
            )
            await db.commit()
        dao.vector_engine.fail = True
        with pytest.raises(RuntimeError, match="synthetic-vector-failure"):
            await dao.replay_lancedb_wal(worker_id="worker-a", limit=1)
        state = await dao.get_lancedb_mutation_state("mutation-1")
        assert state["state"] in {"RETRY_PENDING", "BLOCKED"}
        assert state["vector_state"] != "VECTOR_APPLIED"
        assert state["graph_state"] != "GRAPH_APPLIED"
        assert state["acknowledged_at"] is None

        dao.vector_engine.fail = False
        claim_a = (
            await dao.claim_lancedb_wal_entries(
                worker_id="worker-a", limit=1, lease_seconds=1
            )
        )[0]
        async with engine.transaction() as db:
            await db.execute(
                "UPDATE lancedb_wal SET lease_expires_at = '1970-01-01T00:00:00+00:00' WHERE id = ?",
                ("mutation-1",),
            )
            await db.commit()
        claim_b = (
            await dao.claim_lancedb_wal_entries(
                worker_id="worker-b", limit=1, lease_seconds=60
            )
        )[0]
        assert claim_a["claim_token"] != claim_b["claim_token"]
        assert not await dao.record_lancedb_projection_state(
            "mutation-1",
            worker_id="worker-a",
            claim_token=claim_a["claim_token"],
            projection="VECTOR_APPLIED",
        )
        assert await dao.replay_claimed_lancedb_wal_entry(claim_b, worker_id="worker-b")
        final = await dao.get_lancedb_mutation_state("mutation-1")
        assert final["state"] == "ACKED"
        assert final["vector_state"] == "VECTOR_APPLIED"
        assert final["graph_state"] == "GRAPH_APPLIED"
        assert final["reconciliation_state"] == "ALIGNED"
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata_patch", "seed_vector", "seed_graph", "expected", "state"),
    [
        ({"canonical_agent_id": "tenant-b"}, True, True, "SCOPE_MISMATCH", "BLOCKED"),
        (
            {"payload_version": 2},
            True,
            True,
            "PAYLOAD_OR_VERSION_MISMATCH",
            "RECONCILIATION_REQUIRED",
        ),
        (
            {"expected_vector_projection": False},
            True,
            False,
            "VECTOR_EXTRA",
            "RECONCILIATION_REQUIRED",
        ),
        (
            {"expected_graph_projection": False},
            True,
            True,
            "GRAPH_EXTRA",
            "RECONCILIATION_REQUIRED",
        ),
    ],
)
async def test_reconciliation_mismatch_states_fail_closed(
    tmp_path: Path, metadata_patch, seed_vector, seed_graph, expected, state
):
    dao, engine = await _dao(tmp_path)
    try:
        metadata = {
            "node_id": "node-mismatch",
            "entity_name": "Entity",
            "graph_required": True,
            "canonical_agent_id": "tenant-a",
            "payload_version": 1,
            "expected_vector_projection": True,
            "expected_graph_projection": True,
        }
        metadata.update(metadata_patch)
        async with engine.transaction() as db:
            await db.execute(
                "INSERT INTO lancedb_wal (id, agent_id, vector, metadata) VALUES (?, ?, ?, ?)",
                ("mismatch", "tenant-a", b"\x00\x00\x80?", json.dumps(metadata)),
            )
            await db.commit()
        if seed_vector:
            await dao.vector_engine.upsert(
                node_id="node-mismatch",
                agent_id="tenant-a",
                embedding=[1.0],
                content_hash=None,
            )
        if seed_graph:
            await dao.graph_provider.insert_node(
                node_id="node-mismatch", name="Entity", agent_id="tenant-a"
            )
        claim = (await dao.claim_lancedb_wal_entries(worker_id="worker", limit=1))[0]
        result = await dao.reconcile_lancedb_wal_entry(
            "mismatch", worker_id="worker", claim_token=claim["claim_token"]
        )
        assert result == expected
        durable = await dao.get_lancedb_mutation_state("mismatch")
        assert durable["state"] == state
        assert durable["acknowledged_at"] is None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_unknown_reconciliation_is_never_acknowledged(tmp_path: Path):
    dao, engine = await _dao(tmp_path)
    try:
        async with engine.transaction() as db:
            await db.execute(
                "INSERT INTO lancedb_wal (id, agent_id, vector, metadata) VALUES (?, ?, ?, ?)",
                (
                    "unknown",
                    "tenant-a",
                    b"\x00\x00\x80?",
                    json.dumps({"node_id": "node-unknown"}),
                ),
            )
            await db.commit()

        async def unavailable(*args, **kwargs):
            raise RuntimeError("store-unavailable")

        dao.vector_engine.get_existing_node_ids = unavailable
        claim = (await dao.claim_lancedb_wal_entries(worker_id="worker", limit=1))[0]
        result = await dao.reconcile_lancedb_wal_entry(
            "unknown", worker_id="worker", claim_token=claim["claim_token"]
        )
        assert result == "UNKNOWN_OR_UNVERIFIABLE"
        durable = await dao.get_lancedb_mutation_state("unknown")
        assert durable["state"] == "RECONCILIATION_REQUIRED"
        assert durable["acknowledged_at"] is None
    finally:
        await engine.close()
