"""Cross-tenant RBAC and storage isolation proof across V4 retrieval surfaces.

Proves that tenant/agent B cannot read, search, or traverse tenant/agent A's
canonical V4 SQLite rows, BM25 FTS index, or LanceDB vector index.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from mesa_memory.config import QueueAdmissionPolicy
from mesa_memory.embedding.service import EmbeddingIdentity, EmbeddingService
from mesa_storage.dao import MemoryDAO
from mesa_storage.repositories.catalog import CatalogIdentityNotFoundError
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_workers.projection_worker import process_projection_outbox_once

VEC8 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

TENANT_A = "tenant-alpha"
TENANT_B = "tenant-beta"
AGENT_A = "agent-alpha"
AGENT_B = "agent-beta"
SENTINEL_ENTITY = "TopSecret_Contract_42"
SENTINEL_CONTENT = (
    "TopSecret_Contract_42 governs liability under Turkish law TBK m.49"
)


@pytest_asyncio.fixture
async def dao_env(tmp_path):
    db_path = tmp_path / "rbac_leak.db"
    vec_path = tmp_path / "vec.lance"
    sql = AsyncEngine(str(db_path), max_connections=2)
    identity = EmbeddingIdentity(
        provider="test", model="rbac-vec", version="v1", dimension=8, normalized=True
    )
    vec = VectorEngine(
        str(vec_path),
        max_workers=1,
        embedding_service=EmbeddingService(
            identity=identity,
            provider_fn=lambda _text: list(VEC8),
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
    try:
        yield dao, identity
    finally:
        await vec.close()
        await sql.close()


async def _seed_tenant_a(dao: MemoryDAO, identity: EmbeddingIdentity) -> dict[str, str]:
    await dao.ensure_v4_catalog_scope(
        tenant_id=TENANT_A, workspace_id="ws-a", dataset_id="ds-a"
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id=TENANT_B, workspace_id="ws-b", dataset_id="ds-b"
    )
    session = await dao.create_v4_session(
        tenant_id=TENANT_A,
        workspace_id="ws-a",
        dataset_ids=["ds-a"],
        agent_id=AGENT_A,
        principal_id="principal-a",
    )
    admission = await dao.admit_v4_memory(
        tenant_id=TENANT_A,
        workspace_id="ws-a",
        dataset_id="ds-a",
        agent_id=AGENT_A,
        session_id=session["session_id"],
        document_id="doc-alpha",
        revision_id="rev-alpha-1",
        chunk_id="chunk-alpha-1",
        title="Confidential Contract",
        content_payload=SENTINEL_CONTENT,
        source_ref="file://alpha-contract.txt",
        evidence_span=SENTINEL_CONTENT,
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
        AGENT_A,
        mutation_id,
        [
            {
                "head": SENTINEL_ENTITY,
                "relation": "DAYANIR",
                "tail": "TBK Madde 49",
                "fact_text": SENTINEL_CONTENT,
                "source_span": SENTINEL_CONTENT,
                "confidence": 1.0,
            }
        ],
    )
    await dao.set_mutation_state(AGENT_A, mutation_id, "VALIDATED")
    for _ in range(4):
        res = await process_projection_outbox_once(dao, worker_id="rbac-test", limit=10)
        if res["claimed"] == 0:
            break

    control = await dao.search_v4_memory(
        tenant_id=TENANT_A,
        agent_id=AGENT_A,
        dataset_ids=["ds-a"],
        query=SENTINEL_ENTITY,
        limit=10,
    )
    assert control, "Control failed: tenant A seed data not searchable"
    return {"session_id": session["session_id"], "mutation_id": mutation_id}


class TestCrossTenantIsolation:
    """Prove zero cross-tenant leakage across all V4 retrieval and read surfaces."""

    @pytest.mark.asyncio
    async def test_session_and_mutation_read_isolation(self, dao_env):
        dao, identity = dao_env
        seeded = await _seed_tenant_a(dao, identity)

        assert (
            await dao.get_v4_session(seeded["session_id"], principal_id="principal-b")
            is None
        )
        assert (
            await dao.list_session_mutation_summaries(
                AGENT_B, seeded["session_id"]
            )
            == []
        )

    @pytest.mark.asyncio
    async def test_bm25_lexical_search_isolation(self, dao_env):
        dao, identity = dao_env
        await _seed_tenant_a(dao, identity)

        b_res = await dao.search_v4_memory(
            tenant_id=TENANT_B,
            agent_id=AGENT_B,
            dataset_ids=["ds-b"],
            query=SENTINEL_ENTITY,
            limit=10,
            rrf_weights={"vector": 0.0, "bm25": 1.0, "assertion": 0.0},
        )
        assert b_res == []

    @pytest.mark.asyncio
    async def test_vector_search_isolation(self, dao_env):
        dao, identity = dao_env
        await _seed_tenant_a(dao, identity)

        # Identical query vector (perfect cosine match), different tenant/agent.
        b_res = await dao.search_v4_memory(
            tenant_id=TENANT_B,
            agent_id=AGENT_B,
            dataset_ids=["ds-b"],
            query=SENTINEL_CONTENT,
            limit=10,
            rrf_weights={"vector": 8.0, "bm25": 0.0, "assertion": 0.0},
        )
        assert b_res == []

    @pytest.mark.asyncio
    async def test_cross_tenant_isolation_all_lanes_and_forged_dataset(self, dao_env):
        dao, identity = dao_env
        await _seed_tenant_a(dao, identity)

        # Tenant B's own dataset returns nothing; passing tenant A's dataset_id fails closed.
        b_all = await dao.search_v4_memory(
            tenant_id=TENANT_B,
            agent_id=AGENT_B,
            dataset_ids=["ds-b"],
            query=SENTINEL_ENTITY,
            limit=10,
            rrf_weights={"vector": 8.0, "bm25": 1.0, "assertion": 1.0},
        )
        assert b_all == [], "Cross-tenant leak on tenant B dataset"
        with pytest.raises(CatalogIdentityNotFoundError):
            await dao.search_v4_memory(
                tenant_id=TENANT_B,
                agent_id=AGENT_B,
                dataset_ids=["ds-a"],
                query=SENTINEL_ENTITY,
                limit=10,
                rrf_weights={"vector": 8.0, "bm25": 1.0, "assertion": 1.0},
            )
