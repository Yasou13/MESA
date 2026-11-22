"""Durable V4 document purge contracts: exact scope, erasure, downstream retry, and isolation."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mesa_memory.config import QueueAdmissionPolicy
from mesa_memory.embedding.service import EmbeddingIdentity, EmbeddingService
from mesa_storage.dao import MemoryDAO, PurgeRetryPendingError
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_workers.projection_worker import (
    process_artifact_cleanup_once,
    process_projection_outbox_once,
)

VEC8 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


@dataclass
class FakeGraph:
    active_node_ids: set[tuple[str, str]] = field(default_factory=set)
    active_assertions: set[str] = field(default_factory=set)
    delete_calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    fail_delete: bool = False
    is_initialized: bool = True
    is_operational: bool = True
    db_path: str = "fake-graph"

    async def insert_node(self, node_id: str, name: str, agent_id: str) -> None:
        self.active_node_ids.add((agent_id, node_id))

    async def insert_assertion(self, **kwargs) -> None:
        self.active_assertions.add(str(kwargs["assertion_id"]))

    async def delete_nodes(
        self, *, purge_id: str, agent_id: str, node_ids: list[str]
    ) -> None:
        self.delete_calls.append((purge_id, tuple(node_ids)))
        if self.fail_delete:
            raise RuntimeError("simulated Kuzu delete failure")
        for node_id in node_ids:
            self.active_node_ids.discard((agent_id, node_id))

    async def delete_assertions(self, *, agent_id: str, assertion_ids: list[str]) -> None:
        self.delete_calls.append((agent_id, tuple(assertion_ids)))
        if self.fail_delete:
            raise RuntimeError("simulated Kuzu delete failure")
        for aid in assertion_ids:
            self.active_assertions.discard(aid)

    async def verify_nodes_absent(self, *, agent_id: str, node_ids: list[str]) -> bool:
        return all(
            (agent_id, node_id) not in self.active_node_ids for node_id in node_ids
        )

    async def execute_query(self, *_args, **_kwargs):
        return [[len(self.active_node_ids)]]

    async def search_v4_graph(self, **_kwargs):
        return []


@dataclass
class FakeVector:
    active_node_ids: set[tuple[str, str]] = field(default_factory=set)
    hard_delete_calls: list[tuple[str, str]] = field(default_factory=list)
    fail_hard_delete: bool = False
    is_initialized: bool = True
    semantic_operational: bool = True

    def __post_init__(self) -> None:
        self.embedding_identity = EmbeddingIdentity(
            provider="test", model="fake-vec", version="v1", dimension=8, normalized=True
        )
        self._service = EmbeddingService(
            identity=self.embedding_identity, provider_fn=lambda _t: list(VEC8)
        )

    async def compute_embedding(self, text: str) -> list[float]:
        return list(VEC8)

    async def compute_query_embedding(self, text: str) -> list[float]:
        return list(VEC8)

    async def upsert(
        self,
        *,
        node_id: str,
        agent_id: str,
        embedding: list[float],
        content_hash: str | None = None,
    ) -> None:
        self.active_node_ids.add((agent_id, node_id))

    async def hard_delete(self, node_id: str, agent_id: str) -> None:
        self.hard_delete_calls.append((agent_id, node_id))
        if self.fail_hard_delete:
            raise RuntimeError("simulated vector delete failure")
        self.active_node_ids.discard((agent_id, node_id))

    async def get_active_node_ids(self, agent_id: str | None = None) -> set[str]:
        return {
            node_id
            for stored_agent_id, node_id in self.active_node_ids
            if agent_id is None or stored_agent_id == agent_id
        }

    async def get_existing_node_ids(
        self, agent_id: str, node_ids: list[str]
    ) -> set[str]:
        return {nid for nid in node_ids if (agent_id, nid) in self.active_node_ids}

    async def search(
        self, query_vector, *, limit, agent_id, allowed_node_ids=None, **_kwargs
    ):
        return [
            {
                "node_id": node_id,
                "agent_id": stored_agent_id,
                "_distance": 0.0,
            }
            for stored_agent_id, node_id in self.active_node_ids
            if stored_agent_id == agent_id
            and (allowed_node_ids is None or node_id in allowed_node_ids)
        ][:limit]


async def _seed_doc(
    dao: MemoryDAO,
    identity: EmbeddingIdentity,
    *,
    tenant_id: str,
    agent_id: str,
    workspace_id: str,
    dataset_id: str,
    session_id: str,
    document_id: str,
    content: str,
) -> str:
    admission = await dao.admit_v4_memory(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        agent_id=agent_id,
        session_id=session_id,
        document_id=document_id,
        revision_id=f"{document_id}-r1",
        chunk_id=f"{document_id}-c0",
        title=document_id,
        content_payload=content,
        source_ref=f"file://{document_id}.txt",
        evidence_span=content,
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
    await dao.record_mutation_extraction(
        agent_id,
        mutation_id,
        [
            {
                "head": f"Entity_{document_id}",
                "relation": "REFERENCES",
                "tail": f"Target_{document_id}",
                "fact_text": content,
                "source_span": content,
                "confidence": 1.0,
            }
        ],
    )
    await dao.set_mutation_state(agent_id, mutation_id, "VALIDATED")
    for _ in range(5):
        res = await process_projection_outbox_once(dao, worker_id="purge-seed", limit=10)
        if res["claimed"] == 0:
            break
    return mutation_id


async def _make_env(tmp_path):
    sql = AsyncEngine(str(tmp_path / "purge-contract.db"), max_connections=2)
    await sql.initialize()
    await initialize_schema(sql)
    await initialize_schema(sql)
    graph = FakeGraph()
    vector = FakeVector()
    dao = MemoryDAO(
        sqlite_engine=sql,
        vector_engine=vector,
        graph_provider=graph,
        embedding_enabled=True,
        graph_enabled=True,
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant-a", workspace_id="ws-a", dataset_id="ds-a"
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant-b", workspace_id="ws-b", dataset_id="ds-b"
    )
    sess_a = await dao.create_v4_session(
        tenant_id="tenant-a",
        workspace_id="ws-a",
        dataset_ids=["ds-a"],
        agent_id="agent-a",
        principal_id="principal-a",
    )
    sess_b = await dao.create_v4_session(
        tenant_id="tenant-b",
        workspace_id="ws-b",
        dataset_ids=["ds-b"],
        agent_id="agent-b",
        principal_id="principal-b",
    )
    await _seed_doc(
        dao,
        vector.embedding_identity,
        tenant_id="tenant-a",
        agent_id="agent-a",
        workspace_id="ws-a",
        dataset_id="ds-a",
        session_id=sess_a["session_id"],
        document_id="doc-a1",
        content="Secret clause alpha one",
    )
    await _seed_doc(
        dao,
        vector.embedding_identity,
        tenant_id="tenant-a",
        agent_id="agent-a",
        workspace_id="ws-a",
        dataset_id="ds-a",
        session_id=sess_a["session_id"],
        document_id="doc-a2",
        content="Remaining clause alpha two",
    )
    await _seed_doc(
        dao,
        vector.embedding_identity,
        tenant_id="tenant-b",
        agent_id="agent-b",
        workspace_id="ws-b",
        dataset_id="ds-b",
        session_id=sess_b["session_id"],
        document_id="doc-b1",
        content="Tenant beta protected clause",
    )
    return dao, sql, graph, vector, sess_a["session_id"]


@pytest.mark.asyncio
async def test_successful_exact_scope_purge_erases_text_and_removes_derived_artifacts(
    tmp_path,
):
    dao, sql, graph, vector, _sess_a = await _make_env(tmp_path)
    try:
        result = await dao.purge_v4_document(
            tenant_id="tenant-a", dataset_id="ds-a", document_id="doc-a1"
        )
        assert result["state"] == "PURGED"
        await process_artifact_cleanup_once(dao, worker_id="cleanup-w", limit=20)

        assert graph.delete_calls, "Expected Kuzu cleanup calls"
        assert vector.hard_delete_calls, "Expected vector hard_delete calls"

        async with sql.connection() as db:
            async with db.execute(
                "SELECT sc.content_payload FROM source_chunks sc "
                "JOIN document_revisions dr ON dr.revision_id = sc.revision_id "
                "JOIN v4_catalog_identities c ON c.physical_id = dr.document_id "
                "WHERE c.kind = 'document' AND c.external_id = 'doc-a1'"
            ) as cursor:
                assert (await cursor.fetchone())[0] == ""
            async with db.execute(
                "SELECT COUNT(*) FROM artifact_cleanup_outbox WHERE state != 'COMPLETED'"
            ) as cursor:
                assert (await cursor.fetchone())[0] == 0

        a_hits = await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["ds-a"],
            query="clause alpha",
            limit=10,
        )
        assert {r["document_id"] for r in a_hits} == {"doc-a2"}
        b_hits = await dao.search_v4_memory(
            tenant_id="tenant-b",
            agent_id="agent-b",
            dataset_ids=["ds-b"],
            query="Tenant beta protected",
            limit=10,
        )
        assert {r["document_id"] for r in b_hits} == {"doc-b1"}
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_kuzu_failure_keeps_erased_document_hidden_and_retry_pending(tmp_path):
    dao, sql, graph, _vector, _sess_a = await _make_env(tmp_path)
    graph.fail_delete = True
    try:
        try:
            await dao.purge_v4_document(
                tenant_id="tenant-a", dataset_id="ds-a", document_id="doc-a1"
            )
        except PurgeRetryPendingError:
            pass
        await process_artifact_cleanup_once(dao, worker_id="cleanup-w", limit=20)

        async with sql.connection() as db:
            async with db.execute(
                "SELECT d.status FROM documents d "
                "JOIN v4_catalog_identities c ON c.physical_id = d.document_id "
                "WHERE c.kind = 'document' AND c.external_id = 'doc-a1'"
            ) as cursor:
                assert (await cursor.fetchone())[0] == "PURGED"
            async with db.execute(
                "SELECT sc.content_payload FROM source_chunks sc "
                "JOIN document_revisions dr ON dr.revision_id = sc.revision_id "
                "JOIN v4_catalog_identities c ON c.physical_id = dr.document_id "
                "WHERE c.kind = 'document' AND c.external_id = 'doc-a1'"
            ) as cursor:
                assert (await cursor.fetchone())[0] == ""
            async with db.execute(
                "SELECT c.state FROM artifact_cleanup_outbox c "
                "JOIN artifact_registry r ON r.registry_id = c.registry_id "
                "WHERE r.store_name = 'GRAPH'"
            ) as cursor:
                states = [row[0] for row in await cursor.fetchall()]
        assert states and all(s in {"PENDING", "RETRY_PENDING"} for s in states)

        a_hits = await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["ds-a"],
            query="Secret clause alpha one",
            limit=10,
        )
        assert not any(r["document_id"] == "doc-a1" for r in a_hits)
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_vector_failure_retries_only_missing_cleanup_and_completes(tmp_path):
    dao, sql, graph, vector, _sess_a = await _make_env(tmp_path)
    vector.fail_hard_delete = True
    try:
        try:
            await dao.purge_v4_document(
                tenant_id="tenant-a", dataset_id="ds-a", document_id="doc-a1"
            )
        except PurgeRetryPendingError:
            pass
        await process_artifact_cleanup_once(dao, worker_id="cleanup-w", limit=20)
        graph_calls_after_first_pass = len(graph.delete_calls)

        async with sql.connection() as db:
            async with db.execute(
                "SELECT c.state FROM artifact_cleanup_outbox c "
                "JOIN artifact_registry r ON r.registry_id = c.registry_id "
                "WHERE r.store_name = 'VECTOR'"
            ) as cursor:
                vec_states = [row[0] for row in await cursor.fetchall()]
        assert vec_states and any(s in {"PENDING", "RETRY_PENDING"} for s in vec_states)

        vector.fail_hard_delete = False
        async with sql.transaction() as db:
            await db.execute(
                "UPDATE artifact_cleanup_outbox SET lease_expires_at = datetime('now', '-1 second') "
                "WHERE state = 'RETRY_PENDING'"
            )
            await db.commit()
        await process_artifact_cleanup_once(dao, worker_id="cleanup-w", limit=20)

        async with sql.connection() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM artifact_cleanup_outbox WHERE state != 'COMPLETED'"
            ) as cursor:
                assert (await cursor.fetchone())[0] == 0
        assert len(graph.delete_calls) == graph_calls_after_first_pass
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_purged_document_cannot_be_readmitted_or_resurrected(tmp_path):
    dao, sql, _graph, vector, sess_a = await _make_env(tmp_path)
    try:
        await dao.purge_v4_document(
            tenant_id="tenant-a", dataset_id="ds-a", document_id="doc-a1"
        )
        with pytest.raises(ValueError, match="purged"):
            await dao.admit_v4_memory(
                tenant_id="tenant-a",
                workspace_id="ws-a",
                dataset_id="ds-a",
                agent_id="agent-a",
                session_id=sess_a,
                document_id="doc-a1",
                revision_id="doc-a1-r2",
                chunk_id="doc-a1-r2-c0",
                title="doc-a1",
                content_payload="resurrection attempt",
                source_ref="file://doc-a1.txt",
                evidence_span="resurrection attempt",
                revision_number=2,
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
    finally:
        await sql.close()
