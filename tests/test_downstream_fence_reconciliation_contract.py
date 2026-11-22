"""WAVE-003-V durable downstream fence and reconciliation contract on V4 outbox."""

from __future__ import annotations

from pathlib import Path

import pytest

from mesa_memory.config import QueueAdmissionPolicy
from mesa_memory.embedding.service import EmbeddingIdentity, EmbeddingService
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_workers.projection_worker import process_projection_outbox_once

VEC8 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


class _Vector:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}
        self.fail = False
        self.is_initialized = True
        self.semantic_operational = True
        self.embedding_identity = EmbeddingIdentity(
            provider="test", model="fence-vec", version="v1", dimension=8, normalized=True
        )
        self._service = EmbeddingService(
            identity=self.embedding_identity, provider_fn=lambda _t: list(VEC8)
        )

    async def compute_embedding(self, _text: str) -> list[float]:
        return list(VEC8)

    async def upsert(self, **record):
        if self.fail:
            raise RuntimeError("synthetic-vector-failure")
        self.rows[(record["agent_id"], record["node_id"])] = dict(record)

    async def hard_delete(self, node_id: str, agent_id: str) -> None:
        self.rows.pop((agent_id, node_id), None)

    async def get_existing_node_ids(self, agent_id: str, node_ids: list[str]) -> set[str]:
        return {node_id for node_id in node_ids if (agent_id, node_id) in self.rows}

    async def get_active_node_ids(self, agent_id: str | None = None) -> set[str]:
        return {
            nid for aid, nid in self.rows if agent_id is None or aid == agent_id
        }


class _Graph:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}
        self.assertions: set[str] = set()
        self.fail = False
        self.is_initialized = True
        self.is_operational = True
        self.db_path = "fake-graph"

    @staticmethod
    def _composite_id(agent_id: str, item_id: str) -> str:
        return f"{agent_id}::{item_id}"

    async def insert_node(self, node_id: str, name: str, agent_id: str) -> None:
        if self.fail:
            raise RuntimeError("synthetic-graph-failure")
        self.rows[(agent_id, node_id)] = {"name": name}

    async def insert_assertion(self, **kwargs) -> None:
        if self.fail:
            raise RuntimeError("synthetic-graph-failure")
        self.assertions.add(str(kwargs["assertion_id"]))

    async def delete_nodes(self, *, purge_id: str, agent_id: str, node_ids: list[str]) -> None:
        for nid in node_ids:
            self.rows.pop((agent_id, nid), None)

    async def delete_assertions(self, *, agent_id: str, assertion_ids: list[str]) -> None:
        for aid in assertion_ids:
            self.assertions.discard(aid)

    async def get_existing_node_ids(self, agent_id: str, node_ids: list[str]) -> set[str]:
        return {nid for nid in node_ids if (agent_id, nid) in self.rows}

    async def execute_query(self, *_args, **_kwargs):
        return [[len(self.rows)]]


async def _dao(root: Path):
    engine = AsyncEngine(str(root / "canonical.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    vector = _Vector()
    graph = _Graph()
    dao = MemoryDAO(
        engine,
        vector,
        graph,
        embedding_enabled=True,
        graph_enabled=True,
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
    return dao, engine, vector, graph, sess["session_id"]


async def _admit_validated(dao: MemoryDAO, vector: _Vector, session_id: str, doc_id: str) -> str:
    admission = await dao.admit_v4_memory(
        tenant_id="tenant-a",
        workspace_id="ws-a",
        dataset_id="ds-a",
        agent_id="tenant-a",
        session_id=session_id,
        document_id=doc_id,
        revision_id=f"{doc_id}-r1",
        chunk_id=f"{doc_id}-c0",
        title=doc_id,
        content_payload=f"Canonical payload for {doc_id}",
        source_ref=f"file://{doc_id}.txt",
        evidence_span=f"Canonical payload for {doc_id}",
        revision_number=1,
        chunk_ordinal=0,
        supersedes_revision_id=None,
        metadata={},
        embedding_provider=vector.embedding_identity.provider,
        embedding_model=vector.embedding_identity.model,
        embedding_version=vector.embedding_identity.version,
        embedding_dimension=vector.embedding_identity.dimension,
        embedding_space_id=vector.embedding_identity.embedding_space_id,
        embedding_normalized=True,
        policy=QueueAdmissionPolicy(),
        validation_mode=0,
    )
    mutation_id = admission["response"]["mutation_id"]
    await dao.record_mutation_extraction("tenant-a", mutation_id, [])
    await dao.set_mutation_state("tenant-a", mutation_id, "VALIDATED")
    return mutation_id


@pytest.mark.asyncio
async def test_downstream_failure_keeps_outbox_open_and_stale_fence_cannot_complete(
    tmp_path: Path,
):
    dao, engine, vector, _graph, session_id = await _dao(tmp_path)
    try:
        mutation_id = await _admit_validated(dao, vector, session_id, "doc-fence-1")
        # SQL lane succeeds first.
        res = await process_projection_outbox_once(dao, worker_id="worker-a", limit=1)
        assert res["completed"] == 1

        # VECTOR lane fails -> stays RETRY_PENDING, mutation is not COMMITTED.
        vector.fail = True
        res = await process_projection_outbox_once(dao, worker_id="worker-a", limit=1)
        assert res["retry_pending"] == 1
        summary = await dao.get_mutation_summary(mutation_id)
        assert summary is not None and summary["state"] != "COMMITTED"

        # Make RETRY_PENDING immediately claimable, claim by worker-a, expire lease, reclaim by worker-b.
        vector.fail = False
        async with engine.transaction() as db:
            await db.execute(
                "UPDATE projection_outbox SET lease_expires_at = datetime('now', '-1 second') "
                "WHERE mutation_id = ? AND projection_name = 'VECTOR'",
                (mutation_id,),
            )
            await db.commit()
        claim_a = (
            await dao.claim_projection_outbox(
                worker_id="worker-a", limit=1, lease_seconds=1
            )
        )[0]
        async with engine.transaction() as db:
            await db.execute(
                "UPDATE projection_outbox SET lease_expires_at = '1970-01-01 00:00:00' "
                "WHERE projection_id = ?",
                (claim_a["projection_id"],),
            )
            await db.commit()
        claim_b = (
            await dao.claim_projection_outbox(
                worker_id="worker-b", limit=1, lease_seconds=60
            )
        )[0]
        assert claim_a["claim_token"] != claim_b["claim_token"]
        # Stale worker-a fence MUST be rejected.
        assert not await dao.complete_projection_outbox(
            claim_a["projection_id"],
            worker_id="worker-a",
            claim_token=claim_a["claim_token"],
            outcome="VECTOR_APPLIED",
        )
        # Current worker-b fence succeeds.
        assert await dao.complete_projection_outbox(
            claim_b["projection_id"],
            worker_id="worker-b",
            claim_token=claim_b["claim_token"],
            outcome="VECTOR_APPLIED",
        )
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_reconciliation_detects_missing_vector_artifact_and_repairs(
    tmp_path: Path,
):
    dao, engine, vector, _graph, session_id = await _dao(tmp_path)
    try:
        await _admit_validated(dao, vector, session_id, "doc-parity-1")
        for _ in range(5):
            r = await process_projection_outbox_once(dao, worker_id="worker-a", limit=5)
            if r["claimed"] == 0:
                break
        assert vector.rows, "Expected vector artifact after full projection"

        # Simulate silent vector loss (out-of-band disappearance).
        vector.rows.clear()
        report = await dao.reconcile_v4_projection_parity(limit=100, repair=True)
        assert report["missing_artifacts"] >= 1
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_reconciliation_detects_orphan_physical_vector_and_enqueues_cleanup(
    tmp_path: Path,
):
    dao, engine, vector, _graph, _session_id = await _dao(tmp_path)
    try:
        # Insert a physical vector with no canonical registry owner.
        await vector.upsert(
            node_id="orphan-vec-id",
            agent_id="tenant-a",
            embedding=list(VEC8),
            content_hash="abc",
        )
        report = await dao.reconcile_v4_bidirectional(
            tenant_id="tenant-a",
            agent_id="tenant-a",
            dataset_ids=["ds-a"],
            repair=True,
        )
        assert report["physical_orphans"] >= 1
        assert report["cleanup_enqueued"] >= 1
    finally:
        await engine.close()
