"""Chaos engineering resilience tests for V4 projection and purge failure paths.

Proves that:
1. A LanceDB upsert crash during V4 vector projection leaves canonical SQLite
   consistent and the vector outbox lane in RETRY_PENDING with zero active
   vector registry artifacts.
2. Normal V4 projection commits and becomes searchable (control).
3. A vector crash during document purge keeps canonical text erased,
   the document PURGED and invisible to search, and cleanup RETRY_PENDING.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from mesa_memory.config import QueueAdmissionPolicy
from mesa_memory.embedding.service import EmbeddingIdentity, EmbeddingService
from mesa_storage.dao import MemoryDAO, PurgeRetryPendingError
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_workers.projection_worker import (
    process_artifact_cleanup_once,
    process_projection_outbox_once,
)

VEC8 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
TENANT_ID = "chaos-tenant"
AGENT_ID = "chaos-agent"


@pytest_asyncio.fixture
async def dao_env(tmp_path):
    sql = AsyncEngine(str(tmp_path / "chaos.db"), max_connections=2)
    identity = EmbeddingIdentity(
        provider="test", model="chaos-vec", version="v1", dimension=8, normalized=True
    )
    vec = VectorEngine(
        str(tmp_path / "vec.lance"),
        max_workers=1,
        embedding_service=EmbeddingService(
            identity=identity, provider_fn=lambda _text: list(VEC8)
        ),
    )
    await sql.initialize()
    await initialize_schema(sql)
    await vec.initialize()
    dao = MemoryDAO(
        sqlite_engine=sql,
        vector_engine=vec,
        embedding_enabled=True,
    )
    await dao.initialize()
    await dao.ensure_v4_catalog_scope(
        tenant_id=TENANT_ID, workspace_id="ws-chaos", dataset_id="ds-chaos"
    )
    session = await dao.create_v4_session(
        tenant_id=TENANT_ID,
        workspace_id="ws-chaos",
        dataset_ids=["ds-chaos"],
        agent_id=AGENT_ID,
        principal_id="principal-chaos",
    )
    try:
        yield dao, sql, vec, identity, session["session_id"]
    finally:
        await vec.close()
        await sql.close()


async def _admit_and_validate(
    dao: MemoryDAO,
    identity: EmbeddingIdentity,
    session_id: str,
    *,
    doc_id: str,
    content: str,
) -> str:
    admission = await dao.admit_v4_memory(
        tenant_id=TENANT_ID,
        workspace_id="ws-chaos",
        dataset_id="ds-chaos",
        agent_id=AGENT_ID,
        session_id=session_id,
        document_id=doc_id,
        revision_id=f"{doc_id}-rev1",
        chunk_id=f"{doc_id}-c0",
        title=doc_id,
        content_payload=content,
        source_ref=f"file://{doc_id}.txt",
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
    await dao.record_mutation_extraction(AGENT_ID, mutation_id, [])
    await dao.set_mutation_state(AGENT_ID, mutation_id, "VALIDATED")
    return mutation_id


class TestV4ProjectionAndPurgeChaos:
    """Prove V4 projection compensation and fail-closed purge under store crashes."""

    @pytest.mark.asyncio
    async def test_lancedb_failure_leaves_vector_lane_retryable_without_active_registry(
        self, dao_env
    ):
        dao, sql, vec, identity, session_id = dao_env
        mutation_id = await _admit_and_validate(
            dao,
            identity,
            session_id,
            doc_id="doc-vec-crash",
            content="Orphan vector chaos sentinel text",
        )
        # First claim processes SQL lane cleanly.
        sql_res = await process_projection_outbox_once(dao, worker_id="chaos-w", limit=1)
        assert sql_res["completed"] == 1

        # Crash LanceDB on the VECTOR lane.
        with patch.object(
            vec,
            "upsert",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Simulated LanceDB Crash"),
        ):
            vec_res = await process_projection_outbox_once(
                dao, worker_id="chaos-w", limit=1
            )
        assert vec_res["retry_pending"] == 1

        async with sql.connection() as db:
            async with db.execute(
                "SELECT state, last_error_class FROM projection_outbox "
                "WHERE mutation_id = ? AND projection_name = 'VECTOR'",
                (mutation_id,),
            ) as cursor:
                row = await cursor.fetchone()
            async with db.execute(
                "SELECT COUNT(*) FROM artifact_registry "
                "WHERE store_name = 'VECTOR' AND state = 'ACTIVE'"
            ) as cursor:
                active_vec = (await cursor.fetchone())[0]
        assert row is not None and row[0] == "RETRY_PENDING"
        assert row[1] == "RuntimeError"
        assert active_vec == 0

    @pytest.mark.asyncio
    async def test_successful_projection_commits_and_is_searchable(self, dao_env):
        dao, _sql, _vec, identity, session_id = dao_env
        mutation_id = await _admit_and_validate(
            dao,
            identity,
            session_id,
            doc_id="doc-control",
            content="Control persistent chaos record",
        )
        for _ in range(4):
            res = await process_projection_outbox_once(dao, worker_id="chaos-w", limit=5)
            if res["claimed"] == 0:
                break
        summary = await dao.get_mutation_summary(mutation_id)
        assert summary is not None and summary["state"] == "COMMITTED"
        found = await dao.search_v4_memory(
            tenant_id=TENANT_ID,
            agent_id=AGENT_ID,
            dataset_ids=["ds-chaos"],
            query="Control persistent chaos record",
            limit=5,
        )
        assert any(r["document_id"] == "doc-control" for r in found)

    @pytest.mark.asyncio
    async def test_purge_downstream_failure_keeps_fail_closed_erasure(self, dao_env):
        dao, sql, vec, identity, session_id = dao_env
        await _admit_and_validate(
            dao,
            identity,
            session_id,
            doc_id="doc-purge-crash",
            content="Sensitive purgeable chaos payload",
        )
        for _ in range(4):
            res = await process_projection_outbox_once(dao, worker_id="chaos-w", limit=5)
            if res["claimed"] == 0:
                break

        with patch.object(
            vec,
            "hard_delete",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Simulated Purge Vector Crash"),
        ):
            try:
                await dao.purge_v4_document(
                    tenant_id=TENANT_ID,
                    dataset_id="ds-chaos",
                    document_id="doc-purge-crash",
                )
            except PurgeRetryPendingError:
                pass
            await process_artifact_cleanup_once(dao, worker_id="chaos-cleanup", limit=10)

        async with sql.connection() as db:
            async with db.execute(
                "SELECT d.status FROM documents d "
                "JOIN v4_catalog_identities c ON c.physical_id = d.document_id "
                "WHERE c.kind = 'document' AND c.external_id = 'doc-purge-crash'"
            ) as cursor:
                doc_status = (await cursor.fetchone())[0]
            async with db.execute(
                "SELECT sc.content_payload FROM source_chunks sc "
                "JOIN document_revisions dr ON dr.revision_id = sc.revision_id "
                "JOIN v4_catalog_identities c ON c.physical_id = dr.document_id "
                "WHERE c.kind = 'document' AND c.external_id = 'doc-purge-crash'"
            ) as cursor:
                chunk_text = (await cursor.fetchone())[0]
            async with db.execute(
                "SELECT c.state FROM artifact_cleanup_outbox c "
                "JOIN artifact_registry r ON r.registry_id = c.registry_id "
                "WHERE r.store_name = 'VECTOR'"
            ) as cursor:
                cleanup_states = [row[0] for row in await cursor.fetchall()]

        assert doc_status == "PURGED"
        assert chunk_text == ""
        assert any(s in {"PENDING", "RETRY_PENDING", "BLOCKED"} for s in cleanup_states)
        found = await dao.search_v4_memory(
            tenant_id=TENANT_ID,
            agent_id=AGENT_ID,
            dataset_ids=["ds-chaos"],
            query="Sensitive purgeable chaos payload",
            limit=5,
        )
        assert not any(r["document_id"] == "doc-purge-crash" for r in found)
