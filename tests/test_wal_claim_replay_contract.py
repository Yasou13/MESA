"""WAVE-003 contracts for durable single-owner work claims and V4 outbox replay."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from mesa_memory.config import QueueAdmissionPolicy
from mesa_memory.embedding.service import EmbeddingIdentity
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@dataclass
class _Vector:
    rows: set[tuple[str, str]] = field(default_factory=set)

    async def upsert(self, *, node_id, agent_id, embedding, content_hash=None) -> None:
        self.rows.add((agent_id, node_id))

    async def get_existing_node_ids(
        self, agent_id: str, node_ids: list[str]
    ) -> set[str]:
        return {node_id for node_id in node_ids if (agent_id, node_id) in self.rows}

    async def get_active_node_ids(self, agent_id: str | None = None) -> set[str]:
        return {
            node_id
            for scoped_agent, node_id in self.rows
            if agent_id is None or scoped_agent == agent_id
        }


async def _make_dao(tmp_path) -> tuple[MemoryDAO, AsyncEngine]:
    sql = AsyncEngine(str(tmp_path / "wave003.db"), max_connections=4)
    await sql.initialize()
    await initialize_schema(sql)
    return MemoryDAO(sqlite_engine=sql, vector_engine=_Vector()), sql


@pytest.mark.asyncio
async def test_raw_log_claim_is_single_owner_and_terminal_transition_is_fenced(
    tmp_path,
):
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
            "tenant-a",
            log_id,
            worker_id="other-worker",
            claim_token=claim["claim_token"],
            status="processed",
        )
        assert await dao.transition_claimed_raw_log(
            "tenant-a",
            log_id,
            worker_id=claim["claimed_by"],
            claim_token=claim["claim_token"],
            status="processed",
        )
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_expired_raw_log_and_outbox_claims_are_replayable_once_then_completed(
    tmp_path,
):
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
            await db.commit()

        replay = await dao.claim_raw_log("tenant-a", log_id, worker_id="worker-b")
        assert replay is not None
        assert replay["claimed_by"] == "worker-b"

        identity = EmbeddingIdentity(
            provider="test", model="m", version="v1", dimension=8, normalized=True
        )
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-a", workspace_id="ws-a", dataset_id="ds-a"
        )
        sess = await dao.create_v4_session(
            tenant_id="tenant-a",
            workspace_id="ws-a",
            dataset_ids=["ds-a"],
            agent_id="tenant-a",
            principal_id="p-a",
        )
        admission = await dao.admit_v4_memory(
            tenant_id="tenant-a",
            workspace_id="ws-a",
            dataset_id="ds-a",
            agent_id="tenant-a",
            session_id=sess["session_id"],
            document_id="doc-replay",
            revision_id="rev-replay-1",
            chunk_id="chunk-replay-0",
            title="replay",
            content_payload="outbox claim contract",
            source_ref="file://replay.txt",
            evidence_span="outbox claim contract",
            revision_number=1,
            chunk_ordinal=0,
            supersedes_revision_id=None,
            metadata={},
            embedding_provider=identity.provider,
            embedding_model=identity.model,
            embedding_version=identity.version,
            embedding_dimension=identity.dimension,
            embedding_space_id=identity.embedding_space_id,
            embedding_normalized=True,
            policy=QueueAdmissionPolicy(),
            validation_mode=0,
        )
        mutation_id = admission["response"]["mutation_id"]
        await dao.record_mutation_extraction("tenant-a", mutation_id, [])
        await dao.set_mutation_state("tenant-a", mutation_id, "VALIDATED")

        outbox_claims = await asyncio.gather(
            dao.claim_projection_outbox(worker_id="flusher-a", limit=1),
            dao.claim_projection_outbox(worker_id="flusher-b", limit=1),
        )
        claimed = [entry for batch in outbox_claims for entry in batch]
        assert len(claimed) == 1
        entry = claimed[0]
        assert not await dao.complete_projection_outbox(
            entry["projection_id"],
            worker_id="other-worker",
            claim_token=entry["claim_token"],
            outcome="SQL_APPLIED",
        )
        assert await dao.complete_projection_outbox(
            entry["projection_id"],
            worker_id=entry["claimed_by"],
            claim_token=entry["claim_token"],
            outcome="SQL_APPLIED",
        )
        assert not await dao.complete_projection_outbox(
            entry["projection_id"],
            worker_id=entry["claimed_by"],
            claim_token=entry["claim_token"],
            outcome="SQL_APPLIED",
        )
    finally:
        await sql.close()
