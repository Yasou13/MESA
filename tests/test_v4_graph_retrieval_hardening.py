"""Comprehensive correctness test suite for MESA v4:
- P1: Scoped Kùzu Graph V2 retrieval, multi-hop traversal (1..3 hops), canonical SQLite reconciliation, stale projection filtering, deduplication, ContextBuilder integration.
- P2: Truthful semantic capability reporting and graceful embedding exception mapping to 503.
- P2: Session existence and status oracle elimination in _authorized_v4_session.
"""

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException
from starlette.requests import Request

from mesa_api.v4_router import _authorized_v4_session, create_v4_router
from mesa_memory.context_builder import ContextBuilder
from mesa_memory.embedding.service import (
    EmbeddingIdentity,
    EmbeddingService,
    EmbeddingUnavailableError,
)
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema_artifact
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine


async def _create_test_env(tmp_path, *, agent_id: str = "test-agent", tenant_id: str = "test-tenant"):
    db_file = tmp_path / f"{agent_id}_mesa.db"
    sql = AsyncEngine(str(db_file))
    await sql.initialize()
    await initialize_schema(sql)

    identity = EmbeddingIdentity(
        provider="mock", model="hardening-test", version="v1", dimension=8
    )
    vector = VectorEngine(
        str(tmp_path / f"{agent_id}_vectors.lance"),
        max_workers=1,
        embedding_service=EmbeddingService(identity=identity),
    )
    await vector.initialize()

    graph_path = tmp_path / f"{agent_id}_graph"
    initialize_schema_artifact(str(graph_path))
    graph = KuzuGraphProvider(str(graph_path), max_workers=1)
    await graph.initialize()

    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vector, graph_provider=graph)
    await dao.create_v4_workspace(
        tenant_id=tenant_id,
        workspace_id="default-ws",
        workspace_name="Default Workspace",
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id,
        workspace_id="default-ws",
        dataset_id="default-ds",
    )
    return sql, vector, graph, dao


async def _close_test_env(sql, vector, graph):
    if vector is not None:
        await vector.close()
    if graph is not None:
        await graph.close()
    if sql is not None:
        await sql.close()


async def _ingest_entity_and_assertion(
    dao: MemoryDAO,
    graph: KuzuGraphProvider,
    *,
    tenant_id: str,
    agent_id: str,
    dataset_id: str,
    mutation_id: str,
    subject_name: str,
    predicate: str,
    object_name: str,
    confidence: float = 1.0,
):
    """Helper to ingest canonical entities and assertion in SQLite and project into Kùzu."""
    s_entity = await dao.resolve_v4_entity(
        tenant_id=tenant_id, canonical_name=subject_name
    )
    o_entity = await dao.resolve_v4_entity(
        tenant_id=tenant_id, canonical_name=object_name
    )
    s_id = s_entity["entity_id"]
    o_id = o_entity["entity_id"]

    async with dao._sql.transaction() as db:
        ds_id = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
        )

        pipe_id = f"pipe_{mutation_id}"
        await db.execute(
            "INSERT OR IGNORE INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES (?, ?, 'test-sess', ?, 'COMPLETED')",
            (pipe_id, tenant_id, agent_id),
        )

        # Register mutation
        await db.execute(
            "INSERT OR IGNORE INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, tenant_id, content_payload, state) "
            "VALUES (?, ?, 'sess_1', ?, ?, 'payload', 'COMMITTED')",
            (mutation_id, f"cand_{mutation_id}", agent_id, tenant_id),
        )

        # Register artifact sources
        for eid in (s_id, o_id):
            reg_id = f"reg_{eid}"
            await db.execute(
                "INSERT OR IGNORE INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
                "VALUES (?, ?, ?, 'canonical', 'ENTITY', ?, 'ACTIVE')",
                (reg_id, tenant_id, agent_id, eid),
            )
            await db.execute(
                "INSERT OR IGNORE INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
                "VALUES (?, ?, ?, ?, 'ACTIVE')",
                (f"src_{eid}_{mutation_id}", reg_id, mutation_id, ds_id),
            )

        # Register assertion
        a_id = f"ast_{mutation_id}_{s_id}_{o_id}"
        await db.execute(
            "INSERT OR IGNORE INTO v4_assertions ("
            "assertion_id, tenant_id, dataset_id, subject_id, predicate, object_entity_id, "
            "source_ref, document_id, revision_id, chunk_id, confidence, status, mutation_id, pipeline_run_id"
            ") VALUES (?, ?, ?, ?, ?, ?, '', '', '', '', ?, 'ACTIVE', ?, ?)",
            (a_id, tenant_id, ds_id, s_id, predicate, o_id, confidence, mutation_id, pipe_id),
        )
        await db.commit()

    # Project into Kùzu
    await graph.insert_node(s_id, subject_name, agent_id=agent_id)
    await graph.insert_node(o_id, object_name, agent_id=agent_id)
    await graph.insert_assertion(
        assertion_id=a_id,
        agent_id=agent_id,
        subject_id=s_id,
        predicate=predicate,
        object_id=o_id,
        confidence=confidence,
        status="ACTIVE",
        mutation_id=mutation_id,
    )
    return s_id, o_id, a_id


@pytest.mark.asyncio
async def test_a_provider_wiring(tmp_path):
    """Test A: Verify graph-enabled V4 search invokes real Kùzu search_v4_graph."""
    sql, vector, graph, dao = await _create_test_env(tmp_path)
    try:
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m1",
            subject_name="Alice",
            predicate="leads",
            object_name="Aurora",
        )

        with patch.object(graph, "search_v4_graph", wraps=graph.search_v4_graph) as spy_graph:
            results = await dao.search_v4_memory(
                tenant_id="test-tenant",
                agent_id="test-agent",
                dataset_ids=["default-ds"],
                query="Alice",
                limit=10,
            )
            assert spy_graph.called
            assert len(results) >= 1
            entity_names = {r["entity"]["canonical_name"] for r in results}
            assert "Alice" in entity_names
    finally:
        await _close_test_env(sql, vector, graph)


@pytest.mark.asyncio
async def test_b_real_2_hop_traversal(tmp_path):
    """Test B: Alice -> Project Aurora -> Database HeliosDB (2 hops). Querying Alice discovers HeliosDB."""
    sql, vector, graph, dao = await _create_test_env(tmp_path)
    try:
        # Hop 1: Alice -> Aurora
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m1",
            subject_name="Alice",
            predicate="leads",
            object_name="Aurora",
        )
        # Hop 2: Aurora -> HeliosDB
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m2",
            subject_name="Aurora",
            predicate="uses",
            object_name="HeliosDB",
        )

        results = await dao.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_ids=["default-ds"],
            query="Alice",
            limit=10,
        )
        entity_names = {r["entity"]["canonical_name"] for r in results}
        assert "Alice" in entity_names
        assert "Aurora" in entity_names
        assert "HeliosDB" in entity_names
    finally:
        await _close_test_env(sql, vector, graph)


@pytest.mark.asyncio
async def test_c_real_3_hop_traversal(tmp_path):
    """Test C: Alice -> Aurora -> HeliosDB -> North-7 (3 hops). Querying Alice discovers North-7."""
    sql, vector, graph, dao = await _create_test_env(tmp_path)
    try:
        # Hop 1: Alice -> Aurora
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m1",
            subject_name="Alice",
            predicate="leads",
            object_name="Aurora",
        )
        # Hop 2: Aurora -> HeliosDB
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m2",
            subject_name="Aurora",
            predicate="uses",
            object_name="HeliosDB",
        )
        # Hop 3: HeliosDB -> North-7
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m3",
            subject_name="HeliosDB",
            predicate="hosted_in",
            object_name="North-7",
        )

        results = await dao.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_ids=["default-ds"],
            query="Alice",
            limit=10,
        )
        entity_names = {r["entity"]["canonical_name"] for r in results}
        assert "North-7" in entity_names
    finally:
        await _close_test_env(sql, vector, graph)


@pytest.mark.asyncio
async def test_d_graph_ablation(tmp_path):
    """Test D: Multi-hop evidence appears with graph enabled, disappears when graph is disabled."""
    sql, vector, graph, dao = await _create_test_env(tmp_path)
    try:
        # Hop 1: Alice -> Aurora
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m1",
            subject_name="Alice",
            predicate="leads",
            object_name="Aurora",
        )
        # Hop 2: Aurora -> HeliosDB
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m2",
            subject_name="Aurora",
            predicate="uses",
            object_name="HeliosDB",
        )

        # 1. With graph operational: HeliosDB is found
        results_with_graph = await dao.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_ids=["default-ds"],
            query="Alice",
            limit=10,
        )
        names_with = {r["entity"]["canonical_name"] for r in results_with_graph}
        assert "HeliosDB" in names_with

        # 2. With graph disabled
        dao_no_graph = MemoryDAO(sqlite_engine=sql, vector_engine=vector, graph_provider=None)
        results_no_graph = await dao_no_graph.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_ids=["default-ds"],
            query="Alice",
            limit=10,
        )
        names_without = {r["entity"]["canonical_name"] for r in results_no_graph}
        assert "HeliosDB" not in names_without
        assert "Alice" in names_without
    finally:
        await _close_test_env(sql, vector, graph)


@pytest.mark.asyncio
async def test_e_tenant_isolation_in_graph_traversal(tmp_path):
    """Test E: Tenant A's graph cannot be traversed or seen by Tenant B."""
    sql, vector, graph, dao = await _create_test_env(tmp_path)
    try:
        # Tenant A
        await dao.create_v4_workspace(
            tenant_id="tenant-a", workspace_id="default-ws", workspace_name="WS A"
        )
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-a", workspace_id="default-ws", dataset_id="default-ds"
        )
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="tenant-a",
            agent_id="tenant-a",
            dataset_id="default-ds",
            mutation_id="m_a",
            subject_name="SecretAlpha",
            predicate="protects",
            object_name="VaultAlpha",
        )
        # Tenant B
        await dao.create_v4_workspace(
            tenant_id="tenant-b", workspace_id="default-ws", workspace_name="WS B"
        )
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-b", workspace_id="default-ws", dataset_id="default-ds"
        )
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="tenant-b",
            agent_id="tenant-b",
            dataset_id="default-ds",
            mutation_id="m_b",
            subject_name="SecretBeta",
            predicate="protects",
            object_name="VaultBeta",
        )

        # Search Tenant A for SecretBeta -> 0 results
        res_a = await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="tenant-a",
            dataset_ids=["default-ds"],
            query="SecretBeta",
            limit=10,
        )
        assert len(res_a) == 0

        # Search Tenant B for SecretAlpha -> 0 results
        res_b = await dao.search_v4_memory(
            tenant_id="tenant-b",
            agent_id="tenant-b",
            dataset_ids=["default-ds"],
            query="SecretAlpha",
            limit=10,
        )
        assert len(res_b) == 0
    finally:
        await _close_test_env(sql, vector, graph)


@pytest.mark.asyncio
async def test_f_dataset_isolation_in_graph_retrieval(tmp_path):
    """Test F: Graph candidate in Dataset 2 is excluded when querying Dataset 1 only."""
    sql, vector, graph, dao = await _create_test_env(tmp_path)
    try:
        await dao.ensure_v4_catalog_scope(
            tenant_id="test-tenant", workspace_id="default-ws", dataset_id="dataset-1"
        )
        await dao.ensure_v4_catalog_scope(
            tenant_id="test-tenant", workspace_id="default-ws", dataset_id="dataset-2"
        )

        # Dataset 1: Alice -> Aurora
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="dataset-1",
            mutation_id="m_ds1",
            subject_name="Alice",
            predicate="leads",
            object_name="Aurora",
        )
        # Dataset 2: Aurora -> HeliosDB (HeliosDB is dataset-2 only)
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="dataset-2",
            mutation_id="m_ds2",
            subject_name="Aurora",
            predicate="uses",
            object_name="HeliosDB",
        )

        # Query dataset-1 only: HeliosDB must be filtered out
        results_ds1 = await dao.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_ids=["dataset-1"],
            query="Alice",
            limit=10,
        )
        names_ds1 = {r["entity"]["canonical_name"] for r in results_ds1}
        assert "Alice" in names_ds1
        assert "HeliosDB" not in names_ds1
    finally:
        await _close_test_env(sql, vector, graph)


@pytest.mark.asyncio
async def test_g_stale_deleted_projection_filtering(tmp_path):
    """Test G: Node in Kùzu but deleted in SQLite is filtered out by canonical reconciliation."""
    sql, vector, graph, dao = await _create_test_env(tmp_path)
    try:
        # Ingest Alice -> Aurora -> GhostNode
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m1",
            subject_name="Alice",
            predicate="leads",
            object_name="Aurora",
        )
        s_id, o_id, a_id = await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m2",
            subject_name="Aurora",
            predicate="contacts",
            object_name="GhostNode",
        )

        # Mark GhostNode DELETED in SQLite
        async with dao._sql.transaction() as db:
            await db.execute("UPDATE v4_entities SET status = 'DELETED' WHERE entity_id = ?", (o_id,))
            await db.execute("UPDATE artifact_registry SET state = 'DELETED' WHERE physical_artifact_id = ?", (o_id,))
            await db.commit()

        # Query Alice: GhostNode was traversed in Kùzu but must be discarded during canonical SQLite reconciliation
        results = await dao.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_ids=["default-ds"],
            query="Alice",
            limit=10,
        )
        names = {r["entity"]["canonical_name"] for r in results}
        assert "GhostNode" not in names
        assert "Aurora" in names
    finally:
        await _close_test_env(sql, vector, graph)


@pytest.mark.asyncio
async def test_h_multi_lane_deduplication(tmp_path):
    """Test H: Candidate matched across multiple lanes is deduplicated with combined RRF rank."""
    sql, vector, graph, dao = await _create_test_env(tmp_path)
    try:
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m1",
            subject_name="Quantum",
            predicate="accelerates",
            object_name="Computing",
        )

        results = await dao.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_ids=["default-ds"],
            query="Quantum",
            limit=10,
        )
        entity_ids = [r["entity"]["entity_id"] for r in results]
        assert len(entity_ids) == len(set(entity_ids)), "Every entity in results must be deduplicated"
    finally:
        await _close_test_env(sql, vector, graph)


@pytest.mark.asyncio
async def test_i_context_builder_integration(tmp_path):
    """Test I: ContextBuilder incorporates multi-hop graph retrieved memories into working memory evidence."""
    sql, vector, graph, dao = await _create_test_env(tmp_path)
    try:
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m1",
            subject_name="Alice",
            predicate="leads",
            object_name="Aurora",
        )
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m2",
            subject_name="Aurora",
            predicate="uses",
            object_name="HeliosDB",
        )

        cb = ContextBuilder(dao)
        ctx = await cb.build_context(
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_ids=["default-ds"],
            query="Alice",
            token_budget=1000,
        )
        assert ctx is not None
        formatted = ctx.get("formatted_context", "")
        assert "HeliosDB" in formatted
        assert "Aurora" in formatted
        canonical_entities = {
            m["entity"]["canonical_name"] for m in ctx.get("canonical_memories", [])
        }
        assert "Alice" in canonical_entities
        assert "Aurora" in canonical_entities
        assert "HeliosDB" in canonical_entities
    finally:
        await _close_test_env(sql, vector, graph)


@pytest.mark.asyncio
async def test_j_semantic_capability_and_failure_handling(tmp_path):
    """Test J: Verify semantic truthfulness on unconfigured backend and 503 error mapping on embedding failures."""
    sql, vector, graph, dao = await _create_test_env(tmp_path)
    try:
        # 1. EmbeddingService without loaded local model
        unloaded_service = EmbeddingService(
            identity=EmbeddingIdentity(
                provider="local", model="missing-model", version="v1", dimension=8
            )
        )
        assert unloaded_service.is_operational is False

        vec_unloaded = VectorEngine(
            str(tmp_path / "unloaded.lance"),
            embedding_service=unloaded_service,
        )
        assert vec_unloaded.semantic_operational is False
        assert vec_unloaded.semantic_runtime_available is False

        # Ingest one entity so search_v4_memory executes vector search
        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m1",
            subject_name="Alice",
            predicate="leads",
            object_name="Aurora",
        )

        # 2. VectorEngine operational failure mapping to 503 in v4_router
        await dao.create_v4_session(
            tenant_id="test-tenant",
            workspace_id="default-ws",
            dataset_ids=["default-ds"],
            agent_id="test-agent",
            principal_id="test-principal",
            session_id="test-sess-sem",
        )
        access_control = AccessControl(policy_path=str(tmp_path / "rbac_j.db"))
        await access_control.initialize()
        await access_control.grant_scope_role("test-principal", tenant_id="test-tenant", workspace_id="default-ws", dataset_id="default-ds", role="WRITER")
        await access_control.grant_principal_session_access("test-principal", "test-agent", "test-sess-sem", "READ")
        await access_control.grant_access("test-agent", "test-sess-sem", "READ")

        async def attach_principal(request: Request) -> None:
            request.state.principal = SimpleNamespace(
                principal_id="test-principal", principal_type="USER", status="active", roles={"admin": False}
            )

        app = FastAPI(dependencies=[Depends(attach_principal)])
        app.include_router(
            create_v4_router(
                get_dao=lambda: dao,
                get_access_control=lambda: access_control,
            )
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch.object(vector, "compute_query_embedding", side_effect=EmbeddingUnavailableError("embedding backend down")):
                res = await client.post(
                    "/v4/memory/search",
                    json={"session_id": "test-sess-sem", "query": "Alice"},
                )
                assert res.status_code == 503
                assert res.json()["detail"] == "vector_backend_unavailable"
        await access_control.close()
    finally:
        await _close_test_env(sql, vector, graph)


@pytest.mark.asyncio
async def test_k_session_oracle_elimination(tmp_path):
    """Test K: inaccessible sessions have one externally observable error class."""
    sql = AsyncEngine(str(tmp_path / "session_oracle.db"))
    await sql.initialize()
    await initialize_schema(sql)
    vector = None
    graph = None
    dao = MemoryDAO(sqlite_engine=sql, vector_engine=None, graph_provider=None)
    await dao.create_v4_workspace(
        tenant_id="test-tenant",
        workspace_id="default-ws",
        workspace_name="Default Workspace",
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id="test-tenant",
        workspace_id="default-ws",
        dataset_id="default-ds",
    )
    try:
        access_control = AccessControl(policy_path=str(tmp_path / "rbac.db"))
        await access_control.initialize()

        active_sess = await dao.create_v4_session(
            tenant_id="test-tenant",
            workspace_id="default-ws",
            dataset_ids=["default-ds"],
            agent_id="test-agent",
            principal_id="owner-principal",
            session_id="sess-active-101",
        )
        ended_sess = await dao.create_v4_session(
            tenant_id="test-tenant",
            workspace_id="default-ws",
            dataset_ids=["default-ds"],
            agent_id="test-agent",
            principal_id="owner-principal",
            session_id="sess-ended-202",
        )
        await dao.end_v4_session("sess-ended-202")

        active_session_id = active_sess["session_id"]
        ended_session_id = ended_sess["session_id"]

        await access_control.grant_scope_role("owner-principal", tenant_id="test-tenant", workspace_id="default-ws", dataset_id="default-ds", role="WRITER")
        await access_control.grant_principal_session_access("owner-principal", "test-agent", active_session_id, "WRITE")
        await access_control.grant_principal_session_access("owner-principal", "test-agent", ended_session_id, "WRITE")
        await access_control.grant_access("test-agent", active_session_id, "WRITE")
        await access_control.grant_access("test-agent", ended_session_id, "WRITE")

        def make_request(principal_id: str):
            req = Request(scope={"type": "http", "headers": []})
            req.state.principal = SimpleNamespace(
                principal_id=principal_id,
                status="active",
                roles={"admin": False},
            )
            return req

        inaccessible_errors = []
        for probed_session_id in (
            "nonexistent-session",
            active_session_id,
            ended_session_id,
        ):
            with pytest.raises(HTTPException) as exc:
                await _authorized_v4_session(
                    make_request("foreign-principal"),
                    dao,
                    access_control,
                    probed_session_id,
                    level="WRITE",
                )
            inaccessible_errors.append((exc.value.status_code, exc.value.detail))

        assert inaccessible_errors == [
            (404, "Unknown session"),
            (404, "Unknown session"),
            (404, "Unknown session"),
        ]

        # 4. Authorized owner writing to ENDED session: gets 409
        with pytest.raises(HTTPException) as exc:
            await _authorized_v4_session(
                make_request("owner-principal"),
                dao,
                access_control,
                ended_session_id,
                level="WRITE",
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "Session is not active"

        # 5. Authorized owner writing to ACTIVE session: succeeds
        session = await _authorized_v4_session(
            make_request("owner-principal"),
            dao,
            access_control,
            active_session_id,
            level="WRITE",
        )
        assert session["session_id"] == active_session_id
        await access_control.close()
    finally:
        await _close_test_env(sql, vector, graph)
