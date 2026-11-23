"""V4 durable dead-letter, single-owner claim, and fenced completion contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mesa_memory.config import config
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


async def _make_v4_dao(tmp_path: Path) -> tuple[AsyncEngine, MemoryDAO]:
    engine = AsyncEngine(str(tmp_path / "dlq_v4.db"))
    await engine.initialize()
    await initialize_schema(engine)

    class _FakeVec:
        is_initialized = True
        embedding_identity = None

        async def upsert(self, *args, **kwargs):
            return None

        async def bulk_upsert(self, *args, **kwargs):
            return 0

        async def hard_delete(self, *args, **kwargs):
            return None

        async def search(self, query_vector, *, agent_id, allowed_node_ids=None, limit=10):
            return []

    class _FakeGraph:
        insert_node = AsyncMock()
        insert_assertion = AsyncMock()
        delete_assertions = AsyncMock()
        delete_nodes = AsyncMock()

    dao = MemoryDAO(
        sqlite_engine=engine,
        vector_engine=_FakeVec(),
        graph_provider=_FakeGraph(),
        graph_enabled=False,
    )
    await dao.initialize()
    return engine, dao


async def _admit_and_validate(
    dao: MemoryDAO,
    *,
    doc_id: str,
    rev_id: str,
    chunk_id: str,
) -> tuple[str, str]:
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant-dlq",
        workspace_id="ws-dlq",
        dataset_id="ds-dlq",
    )
    session = await dao.create_v4_session(
        tenant_id="tenant-dlq",
        workspace_id="ws-dlq",
        dataset_ids=["ds-dlq"],
        agent_id="agent-dlq",
        principal_id="principal-dlq",
    )
    admitted = await dao.admit_v4_memory(
        tenant_id="tenant-dlq",
        workspace_id="ws-dlq",
        dataset_id="ds-dlq",
        agent_id="agent-dlq",
        session_id=session["session_id"],
        document_id=doc_id,
        revision_id=rev_id,
        chunk_id=chunk_id,
        title="DLQ Contract Doc",
        content_payload="Durable outbox payload with bounded retries.",
        source_ref=f"dlq://{doc_id}",
        evidence_span="0:44",
        revision_number=1,
        chunk_ordinal=0,
        supersedes_revision_id=None,
        metadata={},
        embedding_provider="local",
        embedding_model="BAAI/bge-m3",
        embedding_version="v1",
        embedding_dimension=8,
        validation_mode=0,
        policy=config.queue_admission_policy,
    )
    mutation_id = admitted["response"]["mutation_id"]
    pipeline_run_id = admitted["response"]["pipeline_run_id"]
    await dao.record_mutation_extraction("agent-dlq", mutation_id, [])
    await dao.set_mutation_state("agent-dlq", mutation_id, "VALIDATED")
    return mutation_id, pipeline_run_id


@pytest.mark.asyncio
async def test_dlq_claim_is_single_owner_and_ack_is_fenced(tmp_path: Path):
    engine, dao = await _make_v4_dao(tmp_path)
    try:
        mutation_id, _ = await _admit_and_validate(
            dao, doc_id="doc-claim", rev_id="rev-claim", chunk_id="chunk-claim"
        )
        batches = await asyncio.gather(
            dao.claim_projection_outbox(worker_id="worker-a", limit=10),
            dao.claim_projection_outbox(worker_id="worker-b", limit=10),
        )
        claims = [item for batch in batches for item in batch]
        sql_claims = [
            c
            for c in claims
            if c["mutation_id"] == mutation_id and c["projection_name"] == "SQL"
        ]
        assert len(sql_claims) == 1
        claim = sql_claims[0]
        owner = str(claim["claimed_by"])
        other = "worker-b" if owner == "worker-a" else "worker-a"

        # Wrong worker and wrong claim token are fenced out.
        assert not await dao.complete_projection_outbox(
            claim["projection_id"],
            worker_id=other,
            claim_token=claim["claim_token"],
            outcome="SQL_APPLIED",
        )
        assert not await dao.complete_projection_outbox(
            claim["projection_id"],
            worker_id=owner,
            claim_token="wrong-token",
            outcome="SQL_APPLIED",
        )
        # Right owner + token completes cleanly.
        assert await dao.complete_projection_outbox(
            claim["projection_id"],
            worker_id=owner,
            claim_token=claim["claim_token"],
            outcome="SQL_APPLIED",
        )
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_dlq_expiry_reclaims_and_bounded_nack_keeps_poison_evidence(
    tmp_path: Path,
):
    engine, dao = await _make_v4_dao(tmp_path)
    try:
        mutation_id, pipeline_run_id = await _admit_and_validate(
            dao, doc_id="doc-poison", rev_id="rev-poison", chunk_id="chunk-poison"
        )
        first_batch = await dao.claim_projection_outbox(worker_id="worker-a", limit=10)
        sql_claim = next(
            c
            for c in first_batch
            if c["mutation_id"] == mutation_id and c["projection_name"] == "SQL"
        )

        # Expire worker-a lease so worker-b can reclaim.
        async with engine.transaction() as db:
            await db.execute(
                "UPDATE projection_outbox SET lease_expires_at = '1970-01-01T00:00:00+00:00' "
                "WHERE projection_id = ?",
                (sql_claim["projection_id"],),
            )
            await db.commit()

        reclaimed_batch = await dao.claim_projection_outbox(
            worker_id="worker-b", limit=10
        )
        reclaimed = next(
            c for c in reclaimed_batch if c["projection_id"] == sql_claim["projection_id"]
        )
        assert reclaimed["claimed_by"] == "worker-b"
        assert reclaimed["claim_token"] != sql_claim["claim_token"]

        # Exhaust attempts -> terminal DEAD_LETTER preserving error_class without raw secret text.
        current = reclaimed
        for _ in range(10):
            await dao.fail_projection_outbox(
                current["projection_id"],
                worker_id="worker-b",
                claim_token=current["claim_token"],
                error_class="RuntimeError",
                retryable=True,
            )
            async with engine.connection() as db:
                async with db.execute(
                    "SELECT state, attempt_count, last_error_class FROM projection_outbox "
                    "WHERE projection_id = ?",
                    (current["projection_id"],),
                ) as cur:
                    row = await cur.fetchone()
            assert row is not None
            if row["state"] == "DEAD_LETTER":
                break
            async with engine.transaction() as db:
                await db.execute(
                    "UPDATE projection_outbox SET lease_expires_at = '1970-01-01T00:00:00+00:00' "
                    "WHERE projection_id = ?",
                    (current["projection_id"],),
                )
                await db.commit()
            next_batch = await dao.claim_projection_outbox(
                worker_id="worker-b", limit=10
            )
            current = next(
                c for c in next_batch if c["projection_id"] == sql_claim["projection_id"]
            )

        assert row["state"] == "DEAD_LETTER"
        assert row["attempt_count"] >= 3
        assert row["last_error_class"] == "RuntimeError"

        # Operator replay re-queues the dead-lettered pipeline cleanly.
        replayed = await dao.replay_pipeline_run(pipeline_run_id)
        assert replayed is not None
        assert replayed["state"] in {"RETRY_PENDING", "QUEUED", "PENDING", "RUNNING"}
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_dispatch_queue_single_owner_fenced_completion_and_receipt(
    tmp_path: Path,
):
    engine, dao = await _make_v4_dao(tmp_path)
    try:
        await _admit_and_validate(
            dao, doc_id="doc-disp", rev_id="rev-disp", chunk_id="chunk-disp"
        )
        batches = await asyncio.gather(
            dao.claim_dispatch_queue(worker_id="worker-a", limit=10),
            dao.claim_dispatch_queue(worker_id="worker-b", limit=10),
        )
        claims = [item for batch in batches for item in batch]
        assert len(claims) == 1
        claim = claims[0]
        owner = str(claim["claimed_by"])
        other = "worker-b" if owner == "worker-a" else "worker-a"

        assert not await dao.complete_dispatch_queue(
            str(claim["queue_record_id"]),
            worker_id=other,
            claim_token=str(claim["claim_token"]),
            outcome="processed",
            side_effect_verified=True,
        )
        assert await dao.complete_dispatch_queue(
            str(claim["queue_record_id"]),
            worker_id=owner,
            claim_token=str(claim["claim_token"]),
            outcome="processed",
            side_effect_verified=True,
        )
        # No remaining claimable work after verified completion.
        assert await dao.claim_dispatch_queue(worker_id="worker-a", limit=10) == []
    finally:
        await engine.close()
